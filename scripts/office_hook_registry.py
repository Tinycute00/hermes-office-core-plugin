from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


MANAGED_MARKER = "OFFICE_OS_MANAGED_HOOK=1"
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
STATUS_MESSAGES = {
    "SessionStart": "載入 Office OS",
    "UserPromptSubmit": "辨識辦公室需求",
    "Stop": "確認 Office OS 進度",
}


class RegistryError(RuntimeError):
    pass


def absolute(path: str, description: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RegistryError(f"{description} must be absolute: {candidate}")
    return candidate.resolve(strict=False)


def require_plugin_root(path: Path) -> None:
    for relative in (Path("hooks") / "office_hook.py", Path("hooks") / "run-python.ps1"):
        if not (path / relative).is_file():
            raise RegistryError(f"plugin root is missing {relative}: {path}")


def require_regular_file(path: Path, description: str) -> None:
    if not path.exists():
        return
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise RegistryError(f"{description} must be a regular file: {path}")
    if details.st_nlink != 1:
        raise RegistryError(f"{description} must not be hard-linked: {path}")


def read_config(path: Path) -> dict[str, Any]:
    require_regular_file(path, "hook config")
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RegistryError(f"hook config is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise RegistryError(f"hook config must be a JSON object: {path}")
    return value


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def powershell_quote(value: str) -> str:
    return value.replace("'", "''")


def commands(plugin_root: Path, data_root: Path) -> tuple[str, str]:
    hook = plugin_root / "hooks" / "office_hook.py"
    bootstrap = plugin_root / "hooks" / "run-python.ps1"
    posix = (
        f"{MANAGED_MARKER} PLUGIN_ROOT={shell_quote(os.fspath(plugin_root))} "
        f"PLUGIN_DATA={shell_quote(os.fspath(data_root))} "
        f"python3 {shell_quote(os.fspath(hook))}"
    )
    windows_script = (
        "$env:OFFICE_OS_MANAGED_HOOK=1; "
        f"$env:PLUGIN_ROOT='{powershell_quote(os.fspath(plugin_root))}'; "
        f"$env:PLUGIN_DATA='{powershell_quote(os.fspath(data_root))}'; "
        f"& '{powershell_quote(os.fspath(bootstrap))}' "
        f"'{powershell_quote(os.fspath(hook))}'"
    )
    windows = (
        "powershell -NoProfile -ExecutionPolicy Bypass -Command "
        f'"{windows_script}"'
    )
    return posix, windows


def managed_group(plugin_root: Path, data_root: Path, event: str) -> dict[str, Any]:
    command, command_windows = commands(plugin_root, data_root)
    group: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "commandWindows": command_windows,
                "timeout": 10,
                "statusMessage": STATUS_MESSAGES[event],
            }
        ]
    }
    if event == "SessionStart":
        group["matcher"] = "startup|resume|clear|compact"
    return group


def is_managed_group(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    handlers = value.get("hooks")
    if not isinstance(handlers, list):
        return False
    for handler in handlers:
        if not isinstance(handler, dict):
            continue
        command = handler.get("command")
        command_windows = handler.get("commandWindows")
        if isinstance(command, str) and MANAGED_MARKER in command:
            return True
        if isinstance(command_windows, str) and "OFFICE_OS_MANAGED_HOOK=1" in command_windows:
            return True
    return False


def hook_groups(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("hooks")
    if value is None:
        groups: dict[str, Any] = {}
        config["hooks"] = groups
        return groups
    if not isinstance(value, dict):
        raise RegistryError("hook config field 'hooks' must be a JSON object")
    return value


def install(config: dict[str, Any], plugin_root: Path, data_root: Path) -> None:
    groups = hook_groups(config)
    for event in EVENTS:
        existing = groups.get(event, [])
        if not isinstance(existing, list):
            raise RegistryError(f"hook config event must be an array: {event}")
        groups[event] = [group for group in existing if not is_managed_group(group)]
        groups[event].append(managed_group(plugin_root, data_root, event))


def uninstall(config: dict[str, Any]) -> None:
    groups = config.get("hooks")
    if groups is None:
        return
    if not isinstance(groups, dict):
        raise RegistryError("hook config field 'hooks' must be a JSON object")
    for event in EVENTS:
        existing = groups.get(event)
        if existing is None:
            continue
        if not isinstance(existing, list):
            raise RegistryError(f"hook config event must be an array: {event}")
        remaining = [group for group in existing if not is_managed_group(group)]
        if remaining:
            groups[event] = remaining
        else:
            del groups[event]
    if not groups:
        del config["hooks"]


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.office-os-{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Office OS lifecycle hooks globally.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--plugin-root", required=True)
    install_parser.add_argument("--data-root")
    for candidate in (install_parser, subparsers.add_parser("uninstall")):
        candidate.add_argument("--config")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config_path = absolute(
        args.config or os.fspath(Path.home() / ".codex" / "hooks.json"), "hook config"
    )
    try:
        config = read_config(config_path)
        if args.action == "install":
            plugin_root = absolute(args.plugin_root, "plugin root")
            data_root = absolute(
                args.data_root
                or os.fspath(Path.home() / ".codex" / "plugin-data" / "office-os"),
                "plugin data root",
            )
            require_plugin_root(plugin_root)
            install(config, plugin_root, data_root)
            result = {
                "action": "installed",
                "config": os.fspath(config_path),
                "pluginData": os.fspath(data_root),
            }
        else:
            uninstall(config)
            result = {"action": "uninstalled", "config": os.fspath(config_path)}
        write_config(config_path, config)
    except (OSError, RegistryError) as error:
        print(f"Office OS hook registry: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from office_hook_activation import (
    MANAGED_MARKER,
    activate_install,
    activate_uninstall,
    is_managed_group,
    is_managed_handler,
    validate_activation_paths,
)
from office_hook_toml import TomlError, atomic_write, require_regular_file
HOOKS_DIRECTORY = Path(__file__).resolve().parents[1] / "hooks"
if os.fspath(HOOKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(HOOKS_DIRECTORY))

from office_hook_spec import HOOK_DEFINITIONS, HookDefinition


class RegistryError(RuntimeError):
    pass


def absolute(path: str, description: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RegistryError(f"{description} must be absolute: {candidate}")
    return candidate.absolute()


def require_plugin_root(path: Path) -> None:
    for relative in (
        Path("hooks") / "office_hook_spec.py",
        Path("hooks") / "run-python.ps1",
    ):
        if not (path / relative).is_file():
            raise RegistryError(f"plugin root is missing {relative}: {path}")


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


def commands(
    plugin_root: Path, data_root: Path, definition: HookDefinition
) -> tuple[str, str]:
    hook = plugin_root / "hooks" / definition.entrypoint
    bootstrap = plugin_root / "hooks" / "run-python.ps1"
    posix = (
        f"{MANAGED_MARKER} PLUGIN_ROOT={shell_quote(os.fspath(plugin_root))} "
        f"PLUGIN_DATA={shell_quote(os.fspath(data_root))} "
        f"python3 {shell_quote(os.fspath(hook))}"
    )
    windows = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        f'"{os.fspath(bootstrap)}" '
        f'-ScriptPath "{os.fspath(hook)}" '
        f'-PluginRoot "{os.fspath(plugin_root)}" '
        f'-PluginData "{os.fspath(data_root)}" '
        f'-ManagedMarker "{MANAGED_MARKER}"'
    )
    return posix, windows


def managed_group(
    plugin_root: Path, data_root: Path, definition: HookDefinition
) -> dict[str, Any]:
    command, command_windows = commands(plugin_root, data_root, definition)
    group: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "commandWindows": command_windows,
                "timeout": definition.timeout_seconds,
                "statusMessage": definition.status_message,
            }
        ]
    }
    if definition.matcher is not None:
        group["matcher"] = definition.matcher
    return group


def hook_groups(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("hooks")
    if value is None:
        groups: dict[str, Any] = {}
        config["hooks"] = groups
        return groups
    if not isinstance(value, dict):
        raise RegistryError("hook config field 'hooks' must be a JSON object")
    return value


def without_managed_handlers(groups: list[Any]) -> list[Any]:
    remaining_groups: list[Any] = []
    for group in groups:
        if not is_managed_group(group):
            remaining_groups.append(group)
            continue
        remaining_handlers = [
            handler for handler in group["hooks"] if not is_managed_handler(handler)
        ]
        if remaining_handlers:
            remaining_group = dict(group)
            remaining_group["hooks"] = remaining_handlers
            remaining_groups.append(remaining_group)
    return remaining_groups


def install(config: dict[str, Any], plugin_root: Path, data_root: Path) -> None:
    groups = hook_groups(config)
    for definition in HOOK_DEFINITIONS:
        event = definition.event_name
        existing = groups.get(event, [])
        if not isinstance(existing, list):
            raise RegistryError(f"hook config event must be an array: {event}")
        groups[event] = without_managed_handlers(existing)
        groups[event].append(managed_group(plugin_root, data_root, definition))


def uninstall(config: dict[str, Any]) -> None:
    groups = config.get("hooks")
    if groups is None:
        return
    if not isinstance(groups, dict):
        raise RegistryError("hook config field 'hooks' must be a JSON object")
    for event, existing in list(groups.items()):
        if not isinstance(existing, list):
            raise RegistryError(f"hook config event must be an array: {event}")
        remaining = without_managed_handlers(existing)
        if remaining:
            groups[event] = remaining
        else:
            del groups[event]
    if not groups:
        del config["hooks"]


def write_config(path: Path, config: dict[str, Any]) -> None:
    atomic_write(
        path,
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "hook config",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Office OS lifecycle hooks globally.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--plugin-root", required=True)
    install_parser.add_argument("--data-root")
    uninstall_parser = subparsers.add_parser("uninstall")
    uninstall_parser.add_argument("--data-root")
    for candidate in (install_parser, uninstall_parser):
        candidate.add_argument("--config")
        candidate.add_argument("--activate", action="store_true")
        candidate.add_argument("--codex-config")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        config_path = absolute(
            args.config or os.fspath(Path.home() / ".codex" / "hooks.json"), "hook config"
        )
        data_root = absolute(
            args.data_root
            or os.fspath(Path.home() / ".codex" / "plugin-data" / "office-os"),
            "plugin data root",
        )
        if args.activate:
            codex_config = absolute(
                args.codex_config or os.fspath(Path.home() / ".codex" / "config.toml"),
                "Codex config",
            )
        else:
            codex_config = None
        if args.action == "install":
            plugin_root = absolute(args.plugin_root, "plugin root")
            require_plugin_root(plugin_root)
        if args.activate:
            validate_activation_paths(
                codex_config, data_root, installing=args.action == "install"
            )
        config = read_config(config_path)
        if args.action == "install":
            install(config, plugin_root, data_root)
            write_config(config_path, config)
            activation_changed = (
                activate_install(config, config_path, codex_config, data_root)
                if args.activate
                else False
            )
            result = {
                "action": "installed",
                "config": os.fspath(config_path),
                "pluginData": os.fspath(data_root),
            }
        else:
            uninstall(config)
            write_config(config_path, config)
            activation_changed = (
                activate_uninstall(config_path, codex_config, data_root)
                if args.activate
                else False
            )
            result = {"action": "uninstalled", "config": os.fspath(config_path)}
        result.update(
            {
                "activation": (
                    "activated"
                    if args.action == "install" and args.activate
                    else "deactivated"
                    if args.action == "uninstall" and args.activate
                    else "not_requested"
                ),
                "activated": args.action == "install" and args.activate,
                "activationChanged": activation_changed,
            }
        )
    except (OSError, RegistryError, TomlError) as error:
        print(f"Office OS hook registry: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Literal, NotRequired, TypedDict

from office_hook_toml import TomlError, atomic_write


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIRECTORY = ROOT / "hooks"
if os.fspath(HOOKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(HOOKS_DIRECTORY))

from office_hook_spec import HOOK_DEFINITIONS, HookDefinition


class HookHandler(TypedDict):
    type: Literal["command"]
    command: str
    commandWindows: str
    timeout: int
    statusMessage: str


class HookGroup(TypedDict):
    hooks: list[HookHandler]
    matcher: NotRequired[str]


class HookConfiguration(TypedDict):
    hooks: dict[str, list[HookGroup]]


def bundled_commands(definition: HookDefinition) -> tuple[str, str]:
    posix = (
        'PLUGIN_ROOT="${PLUGIN_ROOT}" PLUGIN_DATA="${PLUGIN_DATA}" '
        f'python3 "${{PLUGIN_ROOT}}/hooks/{definition.entrypoint}"'
    )
    windows = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        '"$env:PLUGIN_ROOT\\hooks\\run-python.ps1" '
        f'-ScriptPath "$env:PLUGIN_ROOT\\hooks\\{definition.entrypoint}" '
        '-PluginRoot "$env:PLUGIN_ROOT" '
        '-PluginData "$env:PLUGIN_DATA"'
    )
    return posix, windows


def rendered_group(definition: HookDefinition) -> HookGroup:
    command, command_windows = bundled_commands(definition)
    group: HookGroup = {
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


def render_configuration() -> HookConfiguration:
    return {
        "hooks": {
            definition.event_name: [rendered_group(definition)]
            for definition in HOOK_DEFINITIONS
        }
    }


def rendered_text() -> str:
    return json.dumps(render_configuration(), ensure_ascii=False, indent=2) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the Office OS bundled Hook configuration."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "hooks" / "hooks.json",
    )
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    expected = rendered_text()
    try:
        if args.check:
            if not args.output.exists() or args.output.read_text(encoding="utf-8") != expected:
                print(
                    f"Office OS bundled hooks config is stale: {args.output}",
                    file=sys.stderr,
                )
                return 1
            return 0
        atomic_write(args.output, expected, "bundled hooks config")
    except (OSError, TomlError) as error:
        print(f"Office OS hook renderer: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

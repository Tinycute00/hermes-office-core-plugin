from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Final

from office_hooks.protocol import context_output, plugin_data_context


MCP_TOOL_NAME: Final = "mcp__officecli__officecli"
PRE_TOOL_USE: Final = "PreToolUse"
PERMISSION_REQUEST: Final = "PermissionRequest"
MUTATION_VERBS: Final = frozenset({"set", "add", "remove", "move", "swap"})
SIMPLE_BASH_COMMAND: Final = re.compile(
    r"""^\s*(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+
    (?P<script>"[^"\r\n]+"|'[^'\r\n]+'|[^\s"'`$|&;<>()]+)
    (?:\s+(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s"'`$|&;<>()]+))*\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
GUARD_CONTEXT: Final = (
    "Office OS work must stay in a Core-owned candidate. The local adapter and "
    "Core remain authoritative for candidate validation, confirmation, and write "
    "authority; preserve ordinary Codex approval."
)


def normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def installed_script_paths() -> frozenset[str]:
    root = Path(__file__).resolve().parents[2]
    return frozenset(
        {
            normalized_path(root / "skills" / "office-os" / "scripts" / "office_os.py"),
            normalized_path(root / "scripts" / "officecli_manager.py"),
        }
    )


def valid_mcp_command(tool_input: Any) -> tuple[str, ...] | None:
    if not isinstance(tool_input, dict) or set(tool_input) != {"command"}:
        return None
    command = tool_input["command"]
    if not isinstance(command, list) or not 1 <= len(command) <= 128:
        return None
    if not all(isinstance(item, str) for item in command):
        return None
    return tuple(command)


def recognized_bash(tool_input: Any) -> bool:
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    match = SIMPLE_BASH_COMMAND.fullmatch(command)
    if match is None:
        return False
    token = match.group("script")
    script = token[1:-1] if token[:1] in {"'", '"'} else token
    if not os.path.isabs(script):
        return False
    return normalized_path(script) in installed_script_paths()


def plugin_data_root() -> Path | None:
    configured = os.environ.get("PLUGIN_DATA")
    return Path(os.path.abspath(configured)) if configured else None


def is_contained(root: Path, target: str) -> bool:
    try:
        normalized_root = normalized_path(root)
        normalized_target = normalized_path(target)
        return (
            normalized_target != normalized_root
            and os.path.commonpath((normalized_root, normalized_target)) == normalized_root
        )
    except ValueError:
        return False


def explicit_mutation_escape(
    command: tuple[str, ...], data_root: Path
) -> bool:
    if len(command) < 2 or command[0] not in MUTATION_VERBS:
        return False
    target = command[1]
    return os.path.isabs(target) and not is_contained(
        data_root / "officecli-candidates", target
    )


def denial(event: str, code: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Office OS guard: {code}.",
        }
    }


def recognized_context(event: str, data_root: Path) -> dict[str, Any]:
    return context_output(event, GUARD_CONTEXT + plugin_data_context(data_root))


def handle_tool_guard(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or event not in {
        PRE_TOOL_USE,
        PERMISSION_REQUEST,
    }:
        return {}
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name == MCP_TOOL_NAME:
        command = valid_mcp_command(tool_input)
        if command is None:
            return denial(event, "event_protocol") if event == PRE_TOOL_USE else {}
        data_root = plugin_data_root()
        if data_root is None:
            return denial(event, "launcher_environment") if event == PRE_TOOL_USE else {}
        if explicit_mutation_escape(command, data_root):
            return denial(event, "candidate_validation") if event == PRE_TOOL_USE else {}
        return recognized_context(event, data_root)
    if tool_name == "Bash" and recognized_bash(tool_input):
        data_root = plugin_data_root()
        if data_root is None:
            return denial(event, "launcher_environment") if event == PRE_TOOL_USE else {}
        return recognized_context(event, data_root)
    return {}

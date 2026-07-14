from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any, Final

from office_hooks.protocol import context_output
from office_hooks.state import HookStateError, ensure_ordinary_directory
from office_hooks.storage import cleanup_stale_temps, read_json, state_lock, unlink_state_leaf, write_json
from office_hooks.tool_guard import MCP_TOOL_NAME, recognized_bash, valid_mcp_command


DIAGNOSTIC_NAME: Final = "latest_hook_diagnostic.json"
DIAGNOSTIC_VERSION: Final = 1
DIAGNOSTIC_TTL_SECONDS: Final = 24 * 60 * 60
POST_TOOL_USE: Final = "PostToolUse"
MAX_FAILURE_FRAGMENTS: Final = 8
MAX_FAILURE_CONTENT_ITEMS: Final = 8
MAX_FAILURE_FRAGMENT_CHARS: Final = 512
MAX_FAILURE_TEXT_BYTES: Final = 2048
REMEDIATIONS: Final[dict[str, str]] = {
    "config_trust": "repair_trust",
    "launcher_environment": "restart_plugin",
    "event_protocol": "check_hook_event",
    "state_safety": "repair_private_state",
    "consent_policy": "request_owner_consent",
    "runtime_integrity": "check_runtime",
    "process_timeout": "retry_after_timeout",
    "candidate_validation": "check_candidate",
    "publish_recovery": "check_publish_state",
    "final_reply": "return_required_reply",
}


def recognized_operation(payload: dict[str, Any]) -> bool:
    if payload.get("hook_event_name") != POST_TOOL_USE:
        return False
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name == MCP_TOOL_NAME:
        return valid_mcp_command(tool_input) is not None
    return tool_name == "Bash" and recognized_bash(tool_input)


def failure_text(value: Any) -> str | None:
    if not isinstance(value, dict) or not (
        value.get("isError") is True or value.get("is_error") is True
    ):
        return None
    texts: list[str] = []
    used_bytes = 0

    def append_fragment(item: str) -> None:
        nonlocal used_bytes
        if len(texts) >= MAX_FAILURE_FRAGMENTS:
            return
        separator = 1 if texts else 0
        remaining = MAX_FAILURE_TEXT_BYTES - used_bytes - separator
        if remaining <= 0:
            return
        fragment = item[:MAX_FAILURE_FRAGMENT_CHARS]
        encoded = fragment.encode("utf-8", errors="replace")
        if len(encoded) > remaining:
            fragment = encoded[:remaining].decode("utf-8", errors="ignore")
            encoded = fragment.encode("utf-8")
        if fragment:
            texts.append(fragment)
            used_bytes += separator + len(encoded)

    for key in ("error", "message"):
        item = value.get(key)
        if isinstance(item, str):
            append_fragment(item)
    content = value.get("content")
    if isinstance(content, list):
        for item in content[:MAX_FAILURE_CONTENT_ITEMS]:
            if len(texts) >= MAX_FAILURE_FRAGMENTS:
                break
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                append_fragment(item["text"])
    return " ".join(texts)


def successful_response(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return not (value.get("isError") is True or value.get("is_error") is True) and (
        value.get("isError") is False or value.get("is_error") is False
    )


def classify_failure(value: Any) -> str | None:
    text = failure_text(value)
    if text is None:
        return None
    lowered = text.lower()
    for code, markers in (
        ("config_trust", ("trust", "trusted hash")),
        ("launcher_environment", ("plugin_data", "environment")),
        ("event_protocol", ("protocol", "command must", "malformed")),
        ("state_safety", ("hard link", "linked", "reparse", "state is")),
        ("consent_policy", ("consent", "confirmation", "approval", "permission")),
        ("runtime_integrity", ("checksum", "runtime integrity", "managed runtime")),
        ("process_timeout", ("timed out", "timeout")),
        ("candidate_validation", ("candidate", "outside managed", "path escapes")),
        ("publish_recovery", ("publish", "backup", "output")),
        ("final_reply", ("final reply", "required-final-reply", "canonical")),
    ):
        if any(marker in lowered for marker in markers):
            return code
    return None


def workspace_key(payload: dict[str, Any]) -> str | None:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(cwd)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def receipt(code: str, component: str, event: str, workspace: str | None = None) -> dict[str, Any]:
    now = int(time.time())
    value: dict[str, Any] = {
        "version": DIAGNOSTIC_VERSION,
        "created_at": now,
        "expires_at": now + DIAGNOSTIC_TTL_SECONDS,
        "component": component,
        "event": event,
        "code": code,
        "outcome": "failed",
        "remediation": REMEDIATIONS[code],
    }
    if workspace is not None:
        value["workspace_key"] = workspace
    return value


def receipt_path(data_root: Path) -> Path:
    return data_root / DIAGNOSTIC_NAME


def _cleanup_expired_receipt(data_root: Path) -> None:
    path = receipt_path(data_root)
    value = read_json(path, None)
    if (
        isinstance(value, dict)
        and isinstance(value.get("expires_at"), int)
        and not isinstance(value["expires_at"], bool)
        and value["expires_at"] <= int(time.time())
    ):
        unlink_state_leaf(path, "Office OS latest hook diagnostic", missing_ok=True)


def cleanup_expired_receipt(data_root: Path) -> None:
    with state_lock(data_root) as acquired:
        if not acquired:
            raise HookStateError("Office OS could not lock latest hook diagnostic.")
        cleanup_stale_temps(data_root)
        _cleanup_expired_receipt(data_root)


def write_receipt(data_root: Path, code: str, component: str, event: str, workspace: str | None = None) -> None:
    root = ensure_ordinary_directory(data_root, "plugin data root", create_parents=True)
    with state_lock(root) as acquired:
        if not acquired:
            raise HookStateError("Office OS could not lock latest hook diagnostic.")
        cleanup_stale_temps(root)
        _cleanup_expired_receipt(root)
        write_json(receipt_path(root), receipt(code, component, event, workspace))


def handle_tool_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    if not recognized_operation(payload):
        return {}
    response = payload.get("tool_response")
    code = classify_failure(response)
    if code is None:
        if not successful_response(response):
            return {}
        configured = os.environ.get("PLUGIN_DATA")
        if configured:
            data_root = Path(os.path.abspath(configured))
            if os.path.lexists(data_root):
                cleanup_expired_receipt(
                    ensure_ordinary_directory(data_root, "plugin data root")
                )
        return {}
    configured = os.environ.get("PLUGIN_DATA")
    if not configured:
        return {}
    data_root = Path(os.path.abspath(configured))
    write_receipt(data_root, code, "tool_outcome", POST_TOOL_USE, workspace_key(payload))
    return context_output(
        POST_TOOL_USE,
        f"Office OS detected {code}. Follow the Office OS recovery guidance before retrying.",
    )

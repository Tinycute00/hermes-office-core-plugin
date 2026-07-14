from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

from office_hooks.source_free import canonical_source_free_reply, normalized_message
from office_hooks.state import (
    HookStateError,
    ensure_ordinary_directory,
    plugin_data_root,
    unlink_state_leaf,
    validate_ordinary_ancestors,
)
from office_hooks.storage import cleanup_stale_temps, read_json, state_lock, write_json


MAX_PENDING_INTAKES = 128
PENDING_INTAKE_TTL_SECONDS = 3600
PENDING_INTAKES_NAME = "pending_intakes.json"


def pending_intake_keys(payload: dict[str, Any]) -> tuple[str, str]:
    session_id = str(payload.get("session_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    if not session_id or not turn_id:
        raise HookStateError("Office OS source-free intake requires session_id and turn_id.")
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    turn_key = hashlib.sha256(f"{session_id}\0{turn_id}".encode("utf-8")).hexdigest()
    return session_key, turn_key


def live_pending_intakes(value: Any, now: int) -> list[dict[str, Any]]:
    entries = value.get("entries", []) if isinstance(value, dict) else []
    live: list[dict[str, Any]] = []
    cutoff = now - PENDING_INTAKE_TTL_SECONDS
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        session_key = entry.get("session_key")
        expected = entry.get("expected")
        created_at = entry.get("created_at")
        if (
            isinstance(key, str)
            and re.fullmatch(r"[0-9a-f]{64}", key)
            and isinstance(session_key, str)
            and re.fullmatch(r"[0-9a-f]{64}", session_key)
            and isinstance(expected, str)
            and canonical_source_free_reply(expected)
            and isinstance(created_at, int)
            and not isinstance(created_at, bool)
            and cutoff <= created_at <= now
        ):
            live.append(
                {
                    "key": key,
                    "session_key": session_key,
                    "expected": normalized_message(expected),
                    "created_at": created_at,
                }
            )
    return live[-MAX_PENDING_INTAKES:]


def remember_pending_intake(payload: dict[str, Any], expected: str) -> None:
    data_root = ensure_ordinary_directory(
        plugin_data_root(), "plugin data root", create_parents=True
    )
    cleanup_stale_temps(data_root)
    session_key, key = pending_intake_keys(payload)
    now = int(time.time())
    path = data_root / PENDING_INTAKES_NAME
    with state_lock(data_root) as acquired:
        if not acquired:
            raise HookStateError("Office OS could not lock pending intake state.")
        entries = live_pending_intakes(read_json(path, {"entries": []}), now)
        entries = [entry for entry in entries if entry["session_key"] != session_key]
        entries.append(
            {
                "key": key,
                "session_key": session_key,
                "expected": expected,
                "created_at": now,
            }
        )
        write_json(path, {"entries": entries[-MAX_PENDING_INTAKES:]})


def discard_pending_intake(payload: dict[str, Any]) -> None:
    data_root = plugin_data_root()
    validate_ordinary_ancestors(data_root, "plugin data root")
    path = data_root / PENDING_INTAKES_NAME
    if not os.path.lexists(data_root) or not os.path.lexists(path):
        return
    session_key, _ = pending_intake_keys(payload)
    now = int(time.time())
    with state_lock(data_root) as acquired:
        if not acquired:
            raise HookStateError("Office OS could not lock pending intake state.")
        entries = live_pending_intakes(read_json(path, {"entries": []}), now)
        remaining = [entry for entry in entries if entry["session_key"] != session_key]
        if remaining:
            write_json(path, {"entries": remaining})
        else:
            unlink_state_leaf(path, "Office OS pending intake", missing_ok=True)


def consume_pending_intake(payload: dict[str, Any]) -> str | None:
    data_root = plugin_data_root()
    validate_ordinary_ancestors(data_root, "plugin data root")
    path = data_root / PENDING_INTAKES_NAME
    if not os.path.lexists(data_root) or not os.path.lexists(path):
        return None
    session_key, key = pending_intake_keys(payload)
    now = int(time.time())
    with state_lock(data_root) as acquired:
        if not acquired:
            raise HookStateError("Office OS could not lock pending intake state.")
        entries = live_pending_intakes(read_json(path, {"entries": []}), now)
        matched = next((entry for entry in entries if entry["key"] == key), None)
        if matched is None:
            matched = next(
                (
                    entry
                    for entry in reversed(entries)
                    if entry["session_key"] == session_key
                ),
                None,
            )
        expected = matched["expected"] if matched is not None else None
        matched_key = matched["key"] if matched is not None else None
        remaining = [entry for entry in entries if entry["key"] != matched_key]
        if remaining:
            write_json(path, {"entries": remaining})
        else:
            unlink_state_leaf(path, "Office OS pending intake", missing_ok=True)
        return expected

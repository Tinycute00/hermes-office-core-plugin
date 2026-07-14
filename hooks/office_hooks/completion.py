from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

from office_hooks.pending import consume_pending_intake
from office_hooks.protocol import emit
from office_hooks.source_free import STOP_CORRECTION_PREFIX, normalized_message
from office_hooks.state import ACTIVE_STATUSES, workspace_dir
from office_hooks.storage import read_json, state_lock, write_json


MAX_CONTINUATIONS = 2


def source_free_stop_correction(
    payload: dict[str, Any], expected: str
) -> dict[str, str] | None:
    message = str(payload.get("last_assistant_message") or "")
    normalized = normalized_message(message)
    if normalized == expected:
        return None
    if bool(payload.get("stop_hook_active")):
        return None
    return {
        "decision": "block",
        "reason": (
            f"{STOP_CORRECTION_PREFIX} "
            "Return exactly these two lines and nothing else:\n"
            f"{expected}"
        ),
    }


def handle_stop(payload: dict[str, Any], directory: Path) -> None:
    pending = consume_pending_intake(payload)
    if pending is not None:
        emit(source_free_stop_correction(payload, pending) or {})
        return
    if not os.path.lexists(directory):
        emit({})
        return
    path = directory / "run_state.json"
    with state_lock(directory) as acquired:
        if not acquired:
            emit({})
            return
        state = read_json(path, {})
        if not isinstance(state, dict):
            emit({})
            return
        status = state.get("status")
        if status not in ACTIVE_STATUSES:
            emit({})
            return
        remaining = int(state.get("remaining_units") or 0)
        waiting = bool(state.get("waiting_for_user", False))
        continuations = int(state.get("continuation_count") or 0)
        marker = str(state.get("progress_marker") or "")
        prior_marker = str(state.get("last_stop_marker") or "")
        if (
            status not in {"executing", "validating", "publishing"}
            or remaining <= 0
            or waiting
            or continuations >= MAX_CONTINUATIONS
        ):
            emit({})
            return
        if not marker or marker == prior_marker:
            state["no_progress_stops"] = int(state.get("no_progress_stops") or 0) + 1
            state["updated_at"] = int(time.time())
            write_json(path, state)
            emit({})
            return
        state["continuation_count"] = continuations + 1
        state["last_stop_marker"] = marker
        state["no_progress_stops"] = 0
        state["updated_at"] = int(time.time())
        write_json(path, state)
        emit(
            {
                "decision": "block",
                "reason": (
                    f"Continue $office-os run {state.get('run_id', '')}: "
                    f"finish the next dependency-safe chunk and validate it. "
                    f"{remaining} unit(s) remain. Do not ask the user unless an owner decision is required."
                ),
            }
        )


def handle_completion(payload: dict[str, Any]) -> None:
    if str(payload.get("hook_event_name") or "") != "Stop":
        emit({})
        return
    directory = workspace_dir(
        str(payload.get("cwd") or os.getcwd()), create=False
    )
    handle_stop(payload, directory)

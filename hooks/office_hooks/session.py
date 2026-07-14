from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from office_hooks.protocol import context_output, emit, plugin_data_context
from office_hooks.state import ACTIVE_STATUSES, workspace_dir
from office_hooks.storage import cleanup_stale_temps, read_json


def handle_session_start(payload: dict[str, Any], directory: Path) -> None:
    cleanup_stale_temps(directory)
    state = read_json(directory / "run_state.json", {})
    active = (
        isinstance(state, dict)
        and state.get("status") in ACTIVE_STATUSES
        and not state.get("waiting_for_user", False)
    )
    context = (
        "Office OS is available as $office-os for local Excel, Word, "
        "PowerPoint, PDF, and cross-file work. Reclassify the current turn; "
        "the first visible Office response must begin with an intent classification; named-source replies use the Chinese intent envelope."
        + plugin_data_context(directory.parents[1])
    )
    if active:
        context += (
            f" Active run: {state.get('run_id', 'unknown')}; "
            f"status={state.get('status')}; "
            f"remaining_units={state.get('remaining_units', 0)}. "
            "Resume only when the current request still belongs to this run."
        )
    emit(context_output("SessionStart", context))


def handle_session_context(payload: dict[str, Any]) -> None:
    directory = workspace_dir(
        str(payload.get("cwd") or os.getcwd()), create=False
    )
    handle_session_start(payload, directory)

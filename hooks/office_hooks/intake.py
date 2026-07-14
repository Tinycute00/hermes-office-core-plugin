from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from office_hooks.intent import (
    has_named_local_source,
    is_office_prompt,
    object_hints,
    strip_code,
)
from office_hooks.pending import discard_pending_intake, remember_pending_intake
from office_hooks.protocol import context_output, emit, plugin_data_context
from office_hooks.source_free import (
    STOP_CORRECTION_PREFIX,
    expected_source_free_reply,
    source_free_intake_context,
)
from office_hooks.state import HookStateError, plugin_data_root, plugin_root, workspace_dir
from office_hooks.storage import read_json, write_json


MAX_DEDUP_KEYS = 128


def require_prompt_identity(payload: dict[str, Any]) -> None:
    for name in ("session_id", "turn_id"):
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise HookStateError(
                "Office OS intake requires non-empty session_id and turn_id values."
            )


def prompt_reference(prompt: str) -> tuple[str, ...]:
    cleaned = strip_code(prompt)
    hints = object_hints(cleaned)
    if len(hints) != 1:
        return ("Office.md",)
    object_reference = {
        "Excel": "Excel.md",
        "Word": "Word.md",
        "PowerPoint": "PowerPoint.md",
        "PDF": "PDF.md",
    }[hints[0]]
    return ("Office.md", object_reference)


def remember_prompt(directory: Path, payload: dict[str, Any], prompt: str) -> bool:
    session_id = str(payload.get("session_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    key = hashlib.sha256(
        f"{session_id}\0{turn_id}\0{prompt_digest}".encode("utf-8")
    ).hexdigest()
    path = directory / "hook_dedup.json"
    data = read_json(path, {"keys": []})
    keys = data.get("keys", []) if isinstance(data, dict) else []
    keys = [item for item in keys if isinstance(item, str)]
    if key in keys:
        return False
    keys.append(key)
    write_json(path, {"keys": keys[-MAX_DEDUP_KEYS:]})
    return True


def handle_user_prompt(payload: dict[str, Any]) -> None:
    prompt = str(payload.get("prompt") or "")
    if prompt.lstrip().startswith(STOP_CORRECTION_PREFIX):
        plugin_data_root()
        require_prompt_identity(payload)
        discard_pending_intake(payload)
        return
    if not prompt or not is_office_prompt(prompt):
        discard_pending_intake(payload)
        return
    plugin_data_root()
    require_prompt_identity(payload)
    cwd = str(payload.get("cwd") or os.getcwd())
    if not has_named_local_source(prompt, cwd):
        remember_pending_intake(payload, expected_source_free_reply(prompt))
        emit(
            context_output(
                "UserPromptSubmit",
                source_free_intake_context(prompt) + plugin_data_context(plugin_data_root()),
            )
        )
        return
    discard_pending_intake(payload)
    directory = workspace_dir(cwd)
    if not remember_prompt(directory, payload, prompt):
        return
    current_plugin_root = plugin_root()
    skill_path = current_plugin_root / "skills" / "office-os" / "SKILL.md"
    references = [
        current_plugin_root / "skills" / "office-os" / "references" / name
        for name in prompt_reference(prompt)
    ]
    context = (
        "<office-os-intake>\n"
        "Reply with exactly one final assistant message: "
        "its first line must be the intent envelope, specifically the Chinese intent envelope in this exact shape: "
        "意圖：<值>｜物件：<值>｜權限：<值>｜檢查：<值>. "
        "If clarification is needed, put exactly one short question after the envelope in that same final message; "
        "ask at most one short source question. "
        "Do not make a tool call, read a file or reference, emit a visible preamble, plan, skill announcement, "
        "tool-activity summary, progress message, or separate message before that reply. "
        "Emit no visible preamble, plan, skill announcement, tool-activity summary, or separate progress message; "
        "none may substitute for this final reply. "
        "The prompt names a local source path or folder, so proceed under normal Office routing after classification. "
        "Classify this turn only; prior edit or schedule permission does not carry over. "
        "Invoke $office-os for the workflow. Read "
        f"{skill_path} and the relevant references "
        f"{', '.join(os.fspath(reference) for reference in references)}; "
        "then continue under normal Office routing.\n"
        "</office-os-intake>"
        + plugin_data_context(directory.parents[1])
    )
    emit(context_output("UserPromptSubmit", context))

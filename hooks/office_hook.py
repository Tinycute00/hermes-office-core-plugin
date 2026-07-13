#!/usr/bin/env python3
# noqa: SIZE_OK - one bounded process owns the three registered lifecycle events.
"""Codex lifecycle hook for Office OS.

The hook is intentionally small: detect current-turn Office intent, restore a
compact active-run pointer, and permit at most two evidence-backed Stop
continuations. It never edits Office files.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Iterator


STATE_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "office-os" / "scripts"
if os.fspath(STATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, os.fspath(STATE_SCRIPTS))
from office_state import StateLeafError, open_private_state_leaf
from office_state import unlink_private_state_leaf, validate_private_state_leaf


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


configure_stdio()


SUPPORTED_EXTENSIONS = {
    ".xlsx": "Excel",
    ".xls": "Excel",
    ".xlsm": "Excel",
    ".docx": "Word",
    ".doc": "Word",
    ".docm": "Word",
    ".pptx": "PowerPoint",
    ".ppt": "PowerPoint",
    ".pptm": "PowerPoint",
    ".pdf": "PDF",
}

OBJECT_PATTERNS = {
    "Excel": re.compile(
        r"\b(?:excel|spreadsheet|workbook|worksheet|sheet|xlsx?|xlsm)\b|"
        r"試算表|工作簿|工作表|儲存格|公式|對帳|報表",
        re.IGNORECASE,
    ),
    "Word": re.compile(
        r"\b(?:word|docx?|docm|document|contract|report)\b|"
        r"文件|文檔|合約|報告|公文|段落|章節",
        re.IGNORECASE,
    ),
    "PowerPoint": re.compile(
        r"\b(?:powerpoint|pptx?|pptm|presentation|slide|deck)\b|"
        r"簡報|投影片|幻燈片|母片",
        re.IGNORECASE,
    ),
    "PDF": re.compile(r"\bpdf\b|可攜式文件", re.IGNORECASE),
}

ACTION_PATTERN = re.compile(
    r"\b(?:find|search|open|read|extract|summari[sz]e|analy[sz]e|review|"
    r"check|compare|create|make|write|edit|update|change|fix|format|merge|"
    r"combine|convert|schedule|repeat|recurring|automate)\b|"
    r"找|搜尋|查找|查看|讀取|擷取|摘要|分析|檢查|審閱|比較|建立|製作|"
    r"撰寫|編輯|修改|更新|修正|格式|合併|整合|轉換|排程|定期|循環|自動",
    re.IGNORECASE,
)
SCHEDULE_PATTERN = re.compile(
    r"\b(?:schedule|repeat|recurring|automate|weekly|daily|monthly)\b|排程|定期|循環|自動|每週|每天|每月",
    re.IGNORECASE,
)

FENCED_CODE_PATTERN = re.compile(
    r"(?P<fence>" + chr(96) * 3 + r"|~~~)[^\n]*\n.*?(?P=fence)",
    re.DOTALL,
)
INLINE_CODE_PATTERN = re.compile(
    r"(?<!\w)(" + chr(96) + r"+)(.+?)\1",
    re.DOTALL,
)
EXTENSION_PATTERN = re.compile(
    r"(?<![\w.])[^<>\r\n]*?\.(?:xlsx|xlsm|xls|docx|docm|doc|pptx|pptm|ppt|pdf)\b",
    re.IGNORECASE,
)
LOCAL_PATH_PATTERN = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/]|/[A-Za-z0-9_.-]+|\\\\[A-Za-z0-9_.-]+)"
)
ACTIVE_STATUSES = {"grounding", "agreed", "executing", "validating", "publishing"}
MAX_DEDUP_KEYS = 128
MAX_CONTINUATIONS = 2


class HookStateError(RuntimeError):
    pass


def validate_state_leaf(path: Path, description: str) -> os.stat_result | None:
    try:
        return validate_private_state_leaf(path, description)
    except StateLeafError as error:
        raise HookStateError(str(error)) from error


def open_state_leaf(
    path: Path,
    flags: int,
    description: str,
    *,
    create: bool = False,
    exclusive: bool = False,
) -> int:
    try:
        return open_private_state_leaf(
            path, flags, description, create=create, exclusive=exclusive
        )
    except StateLeafError as error:
        raise HookStateError(str(error)) from error


def unlink_state_leaf(path: Path, description: str, *, missing_ok: bool = False) -> None:
    try:
        unlink_private_state_leaf(path, description, missing_ok=missing_ok)
    except StateLeafError as error:
        raise HookStateError(str(error)) from error


def read_input() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")


def canonical_workspace(cwd: str | None) -> str:
    base = cwd or os.getcwd()
    return os.path.normcase(os.path.realpath(os.path.abspath(base)))


def plugin_data_root() -> Path:
    configured = os.environ.get("PLUGIN_DATA")
    if not configured:
        raise HookStateError("Office OS requires the plugin-owned PLUGIN_DATA value.")
    return Path(os.path.abspath(configured))


def is_linklike(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def validate_ordinary_ancestors(path: Path, description: str) -> None:
    for component in (*reversed(path.parents), path):
        if not os.path.lexists(component):
            continue
        if is_linklike(component) or not stat.S_ISDIR(component.lstat().st_mode):
            raise HookStateError(f"Office OS {description} is linked or invalid.")


def ensure_ordinary_directory(
    path: Path, description: str, create_parents: bool = False
) -> Path:
    validate_ordinary_ancestors(path, description)
    if not os.path.lexists(path):
        path.mkdir(parents=create_parents)
    validate_ordinary_ancestors(path, description)
    return path


def ensure_plugin_data_root() -> Path:
    return ensure_ordinary_directory(
        plugin_data_root(), "plugin data root", create_parents=True
    )


def workspace_dir(cwd: str | None) -> Path:
    canonical = canonical_workspace(cwd)
    workspace_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    workspaces = ensure_ordinary_directory(
        ensure_plugin_data_root() / "workspaces", "workspace state root"
    )
    return ensure_ordinary_directory(
        workspaces / workspace_id, "workspace state directory"
    )


def read_json(path: Path, default: Any) -> Any:
    try:
        descriptor = open_state_leaf(path, os.O_RDONLY, "Office OS hook state")
    except FileNotFoundError:
        return default
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".office-os-{path.name}.{os.getpid()}.tmp")
    validate_state_leaf(path, "Office OS hook state")
    descriptor = open_state_leaf(
        temp_path,
        os.O_WRONLY,
        "Office OS hook temporary",
        create=True,
        exclusive=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        validate_state_leaf(temp_path, "Office OS hook temporary")
        validate_state_leaf(path, "Office OS hook state")
        os.replace(temp_path, path)
        validate_state_leaf(path, "Office OS hook state")
    except (OSError, TypeError, ValueError, HookStateError):
        if os.path.lexists(temp_path):
            unlink_state_leaf(temp_path, "Office OS hook temporary")
        raise


def cleanup_stale_temps(directory: Path, max_age_seconds: int = 3600) -> None:
    cutoff = time.time() - max_age_seconds
    for candidate in directory.glob(".office-os-*.tmp"):
        try:
            validate_state_leaf(candidate, "Office OS hook temporary")
            if candidate.is_file() and candidate.stat().st_mtime <= cutoff:
                unlink_state_leaf(candidate, "Office OS hook temporary")
        except OSError:
            continue


@contextlib.contextmanager
def state_lock(directory: Path) -> Iterator[bool]:
    lock_path = directory / "run-state.lock"
    descriptor = open_state_leaf(
        lock_path, os.O_RDWR, "Office OS hook lock", create=True
    )
    acquired = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        deadline = time.monotonic() + 0.25
        while not acquired:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.025)
        if acquired:
            owner = json.dumps(
                {"pid": os.getpid(), "timestamp": time.time()},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, owner)
            os.ftruncate(descriptor, len(owner))
            os.fsync(descriptor)
        yield acquired
    finally:
        if acquired:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def strip_code(prompt: str) -> str:
    without_fences = FENCED_CODE_PATTERN.sub(" ", prompt)

    def replace_inline(match: re.Match[str]) -> str:
        content = match.group(2)
        if any(extension in content.lower() for extension in SUPPORTED_EXTENSIONS):
            return content
        return " "

    return INLINE_CODE_PATTERN.sub(replace_inline, without_fences)


def object_hints(prompt: str) -> list[str]:
    hints: set[str] = set()
    lower = prompt.lower()
    for extension, object_name in SUPPORTED_EXTENSIONS.items():
        if extension in lower:
            hints.add(object_name)
    for object_name, pattern in OBJECT_PATTERNS.items():
        if pattern.search(prompt):
            hints.add(object_name)
    return sorted(hints)


def is_office_prompt(prompt: str) -> bool:
    cleaned = strip_code(prompt)
    if re.search(r"(?<![\w-])\$office-os\b", cleaned, re.IGNORECASE):
        return True
    if EXTENSION_PATTERN.search(cleaned):
        return True
    return bool(ACTION_PATTERN.search(cleaned) and object_hints(cleaned))


def has_named_local_source(prompt: str) -> bool:
    cleaned = strip_code(prompt)
    return bool(EXTENSION_PATTERN.search(cleaned) or LOCAL_PATH_PATTERN.search(cleaned))


def source_free_intent(prompt: str) -> str:
    cleaned = strip_code(prompt)
    if SCHEDULE_PATTERN.search(cleaned):
        return "排程"
    if re.search(r"\b(?:update|edit|change|fix|format)\b|更新|編輯|修改|修正|格式", cleaned, re.I):
        return "更新"
    if re.search(r"\b(?:create|make|write)\b|建立|製作|撰寫", cleaned, re.I):
        return "建立"
    if re.search(r"\b(?:find|search)\b|找|搜尋|查找", cleaned, re.I):
        return "查找"
    if re.search(r"\b(?:analy[sz]e|compare)\b|分析|比較", cleaned, re.I):
        return "分析"
    return "檢查"


def source_free_object(prompt: str) -> str:
    hints = object_hints(strip_code(prompt))
    if len(hints) == 1:
        return hints[0]
    return "跨檔案"


def source_free_check(prompt: str) -> str:
    cleaned = strip_code(prompt)
    if re.search(r"\b(?:complete|full)\b|完整", cleaned, re.I):
        return "完整"
    if re.search(r"\b(?:enhanced|strong)\b|加強", cleaned, re.I):
        return "加強"
    return "快速"


def source_free_intake_context(prompt: str) -> str:
    object_name = source_free_object(prompt)
    envelope = (
        f"意圖：{source_free_intent(prompt)}｜物件：{object_name}｜權限：唯讀｜"
        f"檢查：{source_free_check(prompt)}"
    )
    question = f"{object_name} 來源檔或資料夾路徑是什麼？"
    return (
        "<office-os-source-free-intake>\n"
        "FIRST USER-VISIBLE RESPONSE MUST BE EXACTLY:\n"
        f"`{envelope}\n{question}`\n"
        "No text may occur before it.\n\n"
        "Do not inspect or alter Office data. Do not call `office_os.py`, OfficeCLI, or an MCP tool; "
        "do not create workspace state, a candidate, an output, or a schedule. Wait for the user to name a local source path or folder.\n\n"
        "Loading this skill to honor an explicit $office-os invocation is allowed, but do not load workflow references "
        "or inspect Office data until the source is named.\n"
        "</office-os-source-free-intake>"
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
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    key = f"{session_id}:{turn_id}:{digest}"
    path = directory / "hook_dedup.json"
    data = read_json(path, {"keys": []})
    keys = data.get("keys", []) if isinstance(data, dict) else []
    keys = [item for item in keys if isinstance(item, str)]
    if key in keys:
        return False
    keys.append(key)
    write_json(path, {"keys": keys[-MAX_DEDUP_KEYS:]})
    return True


def context_output(event: str, context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }


def plugin_data_context(directory: Path) -> str:
    data_root = directory.parents[1]
    return (
        f" Authoritative Office OS PLUGIN_DATA is {os.fspath(data_root)}. "
        "Set PLUGIN_DATA to exactly this path for every office_os.py command; "
        "do not use or invent another data root."
    )


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
        "the first visible Office response line must be the Chinese intent envelope."
        + plugin_data_context(directory)
    )
    if active:
        context += (
            f" Active run: {state.get('run_id', 'unknown')}; "
            f"status={state.get('status')}; "
            f"remaining_units={state.get('remaining_units', 0)}. "
            "Resume only when the current request still belongs to this run."
        )
    emit(context_output("SessionStart", context))


def handle_user_prompt(payload: dict[str, Any], directory: Path) -> None:
    prompt = str(payload.get("prompt") or "")
    if not prompt or not is_office_prompt(prompt):
        return
    if not remember_prompt(directory, payload, prompt):
        return
    if not has_named_local_source(prompt):
        emit(context_output("UserPromptSubmit", source_free_intake_context(prompt)))
        return
    plugin_root = Path(
        os.environ.get("PLUGIN_ROOT")
        or os.environ.get("CLAUDE_PLUGIN_ROOT")
        or Path(__file__).resolve().parents[1]
    )
    skill_path = plugin_root / "skills" / "office-os" / "SKILL.md"
    references = [
        plugin_root / "skills" / "office-os" / "references" / name
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
        + plugin_data_context(directory)
    )
    emit(context_output("UserPromptSubmit", context))


def handle_stop(payload: dict[str, Any], directory: Path) -> None:
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


def main() -> int:
    payload = read_input()
    event = str(payload.get("hook_event_name") or "")
    try:
        directory = workspace_dir(str(payload.get("cwd") or os.getcwd()))
        if event == "SessionStart":
            handle_session_start(payload, directory)
        elif event == "UserPromptSubmit":
            handle_user_prompt(payload, directory)
        elif event == "Stop":
            handle_stop(payload, directory)
        elif event:
            emit({})
    except HookStateError as error:
        sys.stderr.write(f"Office OS hook refused unsafe workspace state: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

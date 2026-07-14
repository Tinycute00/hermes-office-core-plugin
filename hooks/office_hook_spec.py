from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


SESSION_START_MATCHER: Final = "startup|resume|clear|compact"
OFFICE_TOOL_MATCHER: Final = r"^(Bash|mcp__officecli__officecli)$"


@dataclass(frozen=True, slots=True)
class HookDefinition:
    event_name: str
    matcher: str | None
    category: str
    entrypoint: str
    status_message: str
    timeout_seconds: int = 10


HOOK_DEFINITIONS: Final[tuple[HookDefinition, ...]] = (
    HookDefinition(
        event_name="SessionStart",
        matcher=SESSION_START_MATCHER,
        category="session_context",
        entrypoint="session_context_hook.py",
        status_message="載入 Office OS",
    ),
    HookDefinition(
        event_name="UserPromptSubmit",
        matcher=None,
        category="intake_router",
        entrypoint="intake_router_hook.py",
        status_message="辨識辦公室需求",
    ),
    HookDefinition(
        event_name="PreToolUse",
        matcher=OFFICE_TOOL_MATCHER,
        category="tool_guard",
        entrypoint="tool_guard_hook.py",
        status_message="檢查 Office 工具",
    ),
    HookDefinition(
        event_name="PermissionRequest",
        matcher=OFFICE_TOOL_MATCHER,
        category="tool_guard",
        entrypoint="tool_guard_hook.py",
        status_message="確認 Office 權限",
    ),
    HookDefinition(
        event_name="PostToolUse",
        matcher=OFFICE_TOOL_MATCHER,
        category="tool_outcome",
        entrypoint="tool_outcome_hook.py",
        status_message="整理 Office 工具結果",
    ),
    HookDefinition(
        event_name="Stop",
        matcher=None,
        category="completion",
        entrypoint="completion_hook.py",
        status_message="確認 Office OS 進度",
    ),
)
EVENTS: Final[tuple[str, ...]] = tuple(
    definition.event_name for definition in HOOK_DEFINITIONS
)
HOOKS_BY_EVENT: Final[Mapping[str, HookDefinition]] = MappingProxyType(
    {definition.event_name: definition for definition in HOOK_DEFINITIONS}
)

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIRECTORY = ROOT / "hooks"
if os.fspath(HOOKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(HOOKS_DIRECTORY))

from office_hook_spec import (
    EVENTS,
    HOOK_DEFINITIONS,
    HOOKS_BY_EVENT,
    OFFICE_TOOL_MATCHER,
    HookDefinition,
)


EXPECTED_DEFINITIONS = (
    ("SessionStart", "startup|resume|clear|compact", "session_context", "session_context_hook.py", "載入 Office OS"),
    ("UserPromptSubmit", None, "intake_router", "intake_router_hook.py", "辨識辦公室需求"),
    ("PreToolUse", "^(Bash|mcp__officecli__officecli)$", "tool_guard", "tool_guard_hook.py", "檢查 Office 工具"),
    ("PermissionRequest", "^(Bash|mcp__officecli__officecli)$", "tool_guard", "tool_guard_hook.py", "確認 Office 權限"),
    ("PostToolUse", "^(Bash|mcp__officecli__officecli)$", "tool_outcome", "tool_outcome_hook.py", "整理 Office 工具結果"),
    ("Stop", None, "completion", "completion_hook.py", "確認 Office OS 進度"),
)


class HookSpecCase(unittest.TestCase):
    def test_exports_one_ordered_lookup_contract(self) -> None:
        self.assertEqual(EVENTS, tuple(item[0] for item in EXPECTED_DEFINITIONS))
        self.assertEqual(tuple(HOOKS_BY_EVENT), EVENTS)
        self.assertEqual(tuple(HOOKS_BY_EVENT.values()), HOOK_DEFINITIONS)
        self.assertEqual(OFFICE_TOOL_MATCHER, "^(Bash|mcp__officecli__officecli)$")
        self.assertEqual(
            tuple(
                (
                    definition.event_name,
                    definition.matcher,
                    definition.category,
                    definition.entrypoint,
                    definition.status_message,
                )
                for definition in HOOK_DEFINITIONS
            ),
            EXPECTED_DEFINITIONS,
        )
        for definition in HOOK_DEFINITIONS:
            self.assertIsInstance(definition, HookDefinition)
            self.assertEqual(definition.timeout_seconds, 10)

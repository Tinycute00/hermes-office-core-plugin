from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIRECTORY = ROOT / "hooks"
OFFICE_HOOK = HOOKS_DIRECTORY / "office_hook.py"
UNIMPLEMENTED_TOOL_EVENTS = (
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
)

if str(HOOKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIRECTORY))

from office_hook_spec import EVENTS, HOOK_DEFINITIONS, HOOKS_BY_EVENT
from office_hooks.source_free import source_free_intake_context


def load_office_hook() -> ModuleType:
    module_name = "office_hook_dispatch_test_target"
    module_spec = importlib.util.spec_from_file_location(module_name, OFFICE_HOOK)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError("Unable to load office_hook compatibility dispatcher")

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


class HookDispatchTest(unittest.TestCase):
    def test_main_dispatches_every_real_spec_event(self) -> None:
        hook = load_office_hook()
        received: list[dict[str, str]] = []
        emitted: list[dict[str, str]] = []

        def handler(value: dict[str, str]) -> None:
            received.append(value)

        handlers = {
            "session_context": handler,
            "intake_router": handler,
            "completion": handler,
        }

        self.assertEqual(tuple(HOOKS_BY_EVENT), EVENTS)
        self.assertEqual(
            tuple(definition.event_name for definition in HOOK_DEFINITIONS), EVENTS
        )
        self.assertTrue(
            all(
                HOOKS_BY_EVENT[definition.event_name] is definition
                for definition in HOOK_DEFINITIONS
            )
        )
        self.assertEqual(
            tuple(
                definition.event_name
                for definition in HOOK_DEFINITIONS
                if definition.event_name in UNIMPLEMENTED_TOOL_EVENTS
            ),
            UNIMPLEMENTED_TOOL_EVENTS,
        )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(hook, "HANDLERS_BY_CATEGORY", handlers),
            patch.object(hook, "emit", side_effect=emitted.append),
        ):
            for definition in HOOK_DEFINITIONS:
                payload = {"hook_event_name": definition.event_name}
                with patch.object(hook, "read_input", return_value=payload):
                    self.assertEqual(hook.main(), 0)

        expected_routed = [
            {"hook_event_name": definition.event_name}
            for definition in HOOK_DEFINITIONS
            if definition.event_name not in UNIMPLEMENTED_TOOL_EVENTS
        ]
        self.assertEqual(received, expected_routed)
        self.assertEqual(emitted, [{}] * len(UNIMPLEMENTED_TOOL_EVENTS))

    def test_main_noops_real_tool_events_before_state_access(self) -> None:
        hook = load_office_hook()
        emitted: list[dict[str, str]] = []

        self.assertEqual(
            tuple(HOOKS_BY_EVENT[event_name].category for event_name in UNIMPLEMENTED_TOOL_EVENTS),
            ("tool_guard", "tool_guard", "tool_outcome"),
        )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(hook, "emit", side_effect=emitted.append),
        ):
            for event_name in UNIMPLEMENTED_TOOL_EVENTS:
                with patch.object(
                    hook,
                    "read_input",
                    return_value={"hook_event_name": event_name},
                ):
                    self.assertEqual(hook.main(), 0)

        self.assertEqual(emitted, [{}] * len(UNIMPLEMENTED_TOOL_EVENTS))

    def test_main_noops_a_real_unknown_event(self) -> None:
        hook = load_office_hook()
        payload = {"hook_event_name": "UnrecognizedEvent"}
        emitted: list[dict[str, str]] = []

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(hook, "read_input", return_value=payload),
            patch.object(hook, "emit", side_effect=emitted.append),
        ):
            self.assertEqual(hook.main(), 0)

        self.assertEqual(emitted, [{}])

    def test_source_free_context_preserves_explicit_invocation_literal(self) -> None:
        context = source_free_intake_context("schedule an Excel report")

        self.assertIn(
            "Loading this skill to honor an explicit $office-os invocation is allowed",
            context,
        )


if __name__ == "__main__":
    unittest.main()

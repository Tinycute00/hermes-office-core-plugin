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

if str(HOOKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIRECTORY))

from office_hook_spec import HookDefinition
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
    def test_main_routes_a_spec_defined_category_to_its_handler(self) -> None:
        hook = load_office_hook()
        payload = {"hook_event_name": "SyntheticEvent"}
        definition = HookDefinition(
            event_name="SyntheticEvent",
            matcher=None,
            category="session_context",
            entrypoint="session_context_hook.py",
            status_message="Synthetic dispatch",
        )
        received: list[dict[str, str]] = []

        def handler(value: dict[str, str]) -> None:
            received.append(value)

        with (
            patch.object(hook, "HOOKS_BY_EVENT", {definition.event_name: definition}),
            patch.object(hook, "HANDLERS_BY_CATEGORY", {definition.category: handler}),
            patch.object(hook, "read_input", return_value=payload),
        ):
            self.assertEqual(hook.main(), 0)

        self.assertEqual(received, [payload])

    def test_main_noops_a_defined_unimplemented_category_before_state_access(self) -> None:
        hook = load_office_hook()
        payload = {"hook_event_name": "PreToolUse"}
        definition = HookDefinition(
            event_name="PreToolUse",
            matcher="*",
            category="tool_guard",
            entrypoint="tool_guard_hook.py",
            status_message="Tool guard",
        )
        emitted: list[dict[str, str]] = []

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(hook, "HOOKS_BY_EVENT", {definition.event_name: definition}),
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

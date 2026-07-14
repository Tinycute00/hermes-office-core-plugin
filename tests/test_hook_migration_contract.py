from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "hooks"))
from office_hook_spec import EVENTS, HOOKS_BY_EVENT


class HookMigrationContractCase(unittest.TestCase):
    def test_shipped_six_event_contract_replaces_the_legacy_monolith(self) -> None:
        shipped = list(json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"])
        legacy = {"SessionStart", "UserPromptSubmit", "Stop"}
        with self.assertRaises(AssertionError):
            self.assertEqual(legacy, set(shipped))
        self.assertEqual(shipped, ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop"])
        self.assertEqual((tuple(shipped), tuple(HOOKS_BY_EVENT)), (EVENTS, EVENTS))
        self.assertFalse((ROOT / "tests" / ("test_" + "hooks.py")).exists())
        environment = os.environ | {"PLUGIN_ROOT": os.fspath(ROOT)}
        environment.pop("PLUGIN_DATA", None)
        completed = subprocess.run([sys.executable, "-B", ROOT / "hooks" / "office_hook.py"], input='{"hook_event_name":"Unknown"}', text=True, capture_output=True, encoding="utf-8", env=environment, check=False)
        self.assertEqual((completed.returncode, completed.stdout), (0, "{}\n"))

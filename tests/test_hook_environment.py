from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "office_hook.py"


class HookEnvironmentCase(unittest.TestCase):
    def test_hook_requires_plugin_data_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            environment = os.environ.copy()
            environment.pop("PLUGIN_DATA", None)
            environment.pop("CLAUDE_PLUGIN_DATA", None)
            environment["PLUGIN_ROOT"] = os.fspath(ROOT)
            payload = {
                "hook_event_name": "UserPromptSubmit",
                "cwd": os.fspath(workspace),
                "prompt": "review report.xlsx",
            }
            completed = subprocess.run(
                [sys.executable, os.fspath(HOOK)],
                input=json.dumps(payload),
                text=True,
                encoding="utf-8",
                capture_output=True,
                env=environment,
                cwd=workspace,
                check=False,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn("PLUGIN_DATA", completed.stderr)

    def test_claude_plugin_data_cannot_substitute_for_plugin_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            environment = os.environ.copy()
            environment.pop("PLUGIN_DATA", None)
            environment["CLAUDE_PLUGIN_DATA"] = os.fspath(workspace / "wrong-root")
            environment["PLUGIN_ROOT"] = os.fspath(ROOT)
            completed = subprocess.run(
                [sys.executable, os.fspath(HOOK)],
                input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": os.fspath(workspace), "prompt": "review report.xlsx"}),
                text=True, encoding="utf-8", capture_output=True, env=environment, cwd=workspace, check=False,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn("PLUGIN_DATA", completed.stderr)


if __name__ == "__main__":
    unittest.main()

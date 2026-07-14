from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import NotRequired, TypedDict, cast
import unittest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "office_hook_registry.py"


class RegistryResult(TypedDict):
    action: str
    config: str
    pluginData: NotRequired[str]


class HookRegistryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.config = self.base / "codex" / "hooks.json"
        self.data_root = self.base / "plugin data"
        self.data_root.mkdir(parents=True)

    def run_registry(self, action: str, expected_returncode: int = 0) -> RegistryResult:
        command = [
            sys.executable,
            os.fspath(REGISTRY),
            action,
            "--config",
            os.fspath(self.config),
        ]
        if action == "install":
            command.extend(
                [
                    "--plugin-root",
                    os.fspath(ROOT),
                    "--data-root",
                    os.fspath(self.data_root),
                ]
            )
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected_returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return cast(RegistryResult, json.loads(completed.stdout))

    def managed_groups(self, config: dict, event: str) -> list[dict]:
        return [
            group
            for group in config["hooks"][event]
            if "OFFICE_OS_MANAGED_HOOK=1" in group["hooks"][0]["command"]
        ]

    def test_install_is_idempotent_and_uninstall_preserves_unrelated_hooks(self) -> None:
        unrelated = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 user-hook.py",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            },
            "unrelated": {"preserve": True},
        }
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(unrelated), encoding="utf-8")

        first = self.run_registry("install")
        self.assertEqual(first["action"], "installed")
        installed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(installed["unrelated"], {"preserve": True})
        self.assertEqual(installed["hooks"]["UserPromptSubmit"][0], unrelated["hooks"]["UserPromptSubmit"][0])
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            groups = self.managed_groups(installed, event)
            self.assertEqual(len(groups), 1)
            handler = groups[0]["hooks"][0]
            self.assertIn(os.fspath(ROOT), handler["command"])
            self.assertIn(os.fspath(self.data_root), handler["command"])
            self.assertIn("OFFICE_OS_MANAGED_HOOK=1", handler["commandWindows"])
            self.assertIn(os.fspath(ROOT), handler["commandWindows"])
            self.assertIn(os.fspath(self.data_root), handler["commandWindows"])
            self.assertIn("-File", handler["commandWindows"])
            self.assertIn("-ScriptPath", handler["commandWindows"])
            self.assertIn("-PluginRoot", handler["commandWindows"])
            self.assertIn("-PluginData", handler["commandWindows"])
            self.assertNotIn("-Command", handler["commandWindows"])

        self.run_registry("install")
        reinstalled = json.loads(self.config.read_text(encoding="utf-8"))
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            self.assertEqual(len(self.managed_groups(reinstalled, event)), 1)

        removed = self.run_registry("uninstall")
        self.assertEqual(removed["action"], "uninstalled")
        remaining = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(remaining, unrelated)

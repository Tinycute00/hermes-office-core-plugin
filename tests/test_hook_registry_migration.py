from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import TypedDict
import unittest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "office_hook_registry.py"


class HookHandler(TypedDict):
    type: str
    command: str
    timeout: int


class HookGroup(TypedDict):
    matcher: str
    hooks: list[HookHandler]


class HookRegistryMigrationCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.config = self.base / "codex" / "hooks.json"
        self.data_root = self.base / "plugin data"
        self.data_root.mkdir(parents=True)

    def run_registry(self, action: str) -> None:
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
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )

    def mixed_group(self, matcher: str) -> tuple[HookGroup, HookHandler]:
        managed_handler: HookHandler = {
            "type": "command",
            "command": "OFFICE_OS_MANAGED_HOOK=1 python3 old-hook.py",
            "timeout": 10,
        }
        unrelated_handler: HookHandler = {
            "type": "command",
            "command": "python3 preserve.py",
            "timeout": 5,
        }
        return (
            {"matcher": matcher, "hooks": [managed_handler, unrelated_handler]},
            unrelated_handler,
        )

    def test_uninstall_removes_only_marked_handlers_from_mixed_groups(self) -> None:
        mixed_group, unrelated_handler = self.mixed_group("startup")
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps({"hooks": {"PreCompact": [mixed_group]}}), encoding="utf-8"
        )

        self.run_registry("uninstall")

        self.assertEqual(
            json.loads(self.config.read_text(encoding="utf-8")),
            {
                "hooks": {
                    "PreCompact": [
                        {"matcher": "startup", "hooks": [unrelated_handler]}
                    ]
                }
            },
        )

    def test_install_replaces_only_marked_handlers_from_mixed_groups(self) -> None:
        matcher = "^(Bash|mcp__officecli__officecli)$"
        mixed_group, unrelated_handler = self.mixed_group(matcher)
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps({"hooks": {"PreToolUse": [mixed_group]}}), encoding="utf-8"
        )

        self.run_registry("install")

        groups = json.loads(self.config.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        self.assertIn({"matcher": matcher, "hooks": [unrelated_handler]}, groups)
        self.assertEqual(
            sum(
                "OFFICE_OS_MANAGED_HOOK=1" in group["hooks"][0]["command"]
                for group in groups
            ),
            1,
        )

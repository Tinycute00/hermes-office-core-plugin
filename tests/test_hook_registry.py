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
HOOK_CONTRACT = {
    "SessionStart": (
        "startup|resume|clear|compact",
        "session_context_hook.py",
        "載入 Office OS",
    ),
    "UserPromptSubmit": (None, "intake_router_hook.py", "辨識辦公室需求"),
    "PreToolUse": (
        "^(Bash|mcp__officecli__officecli)$",
        "tool_guard_hook.py",
        "檢查 Office 工具",
    ),
    "PermissionRequest": (
        "^(Bash|mcp__officecli__officecli)$",
        "tool_guard_hook.py",
        "確認 Office 權限",
    ),
    "PostToolUse": (
        "^(Bash|mcp__officecli__officecli)$",
        "tool_outcome_hook.py",
        "整理 Office 工具結果",
    ),
    "Stop": (None, "completion_hook.py", "確認 Office OS 進度"),
}


class RegistryResult(TypedDict):
    action: str
    config: str
    pluginData: NotRequired[str]


type NormalizedGroup = tuple[str | None, str, str, int]


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

    def normalized_groups(
        self, hooks: dict, managed: bool
    ) -> dict[str, NormalizedGroup]:
        normalized: dict[str, NormalizedGroup] = {}
        for event in HOOK_CONTRACT:
            groups = hooks[event]
            if managed:
                groups = [
                    group
                    for group in groups
                    if "OFFICE_OS_MANAGED_HOOK=1" in group["hooks"][0]["command"]
                ]
            self.assertEqual(len(groups), 1)
            group = groups[0]
            handler = group["hooks"][0]
            matching_entrypoints = list(
                dict.fromkeys(
                    entrypoint
                    for _, entrypoint, _ in HOOK_CONTRACT.values()
                    if entrypoint in handler["command"]
                )
            )
            self.assertEqual(len(matching_entrypoints), 1)
            normalized[event] = (
                group.get("matcher"),
                matching_entrypoints[0],
                handler["statusMessage"],
                handler["timeout"],
            )
        return normalized

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
        bundled = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(
            self.normalized_groups(bundled["hooks"], managed=False),
            self.normalized_groups(installed["hooks"], managed=True),
        )
        for event, (matcher, entrypoint, status_message) in HOOK_CONTRACT.items():
            groups = self.managed_groups(installed, event)
            self.assertEqual(len(groups), 1)
            group = groups[0]
            if matcher is None:
                self.assertNotIn("matcher", group)
            else:
                self.assertEqual(group["matcher"], matcher)
            handler = groups[0]["hooks"][0]
            self.assertIn(os.fspath(ROOT), handler["command"])
            self.assertIn(os.fspath(self.data_root), handler["command"])
            self.assertIn(entrypoint, handler["command"])
            self.assertIn("OFFICE_OS_MANAGED_HOOK=1", handler["commandWindows"])
            self.assertIn(os.fspath(ROOT), handler["commandWindows"])
            self.assertIn(os.fspath(self.data_root), handler["commandWindows"])
            self.assertIn(entrypoint, handler["commandWindows"])
            self.assertIn("-File", handler["commandWindows"])
            self.assertIn("-ScriptPath", handler["commandWindows"])
            self.assertIn("-PluginRoot", handler["commandWindows"])
            self.assertIn("-PluginData", handler["commandWindows"])
            self.assertNotIn("-Command", handler["commandWindows"])
            self.assertEqual(handler["timeout"], 10)
            self.assertEqual(handler["statusMessage"], status_message)

        first_config = self.config.read_bytes()
        self.run_registry("install")
        reinstalled = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(self.config.read_bytes(), first_config)
        for event in HOOK_CONTRACT:
            self.assertEqual(len(self.managed_groups(reinstalled, event)), 1)

        removed = self.run_registry("uninstall")
        self.assertEqual(removed["action"], "uninstalled")
        remaining = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(remaining, unrelated)

    def test_uninstall_sweeps_managed_groups_from_obsolete_event_keys(self) -> None:
        unrelated_group = {
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 preserve.py",
                    "timeout": 5,
                }
            ]
        }
        obsolete_managed_group = {
            "hooks": [
                {
                    "type": "command",
                    "command": "OFFICE_OS_MANAGED_HOOK=1 python3 old-hook.py",
                    "timeout": 10,
                }
            ]
        }
        config = {
            "hooks": {
                "PreCompact": [unrelated_group, obsolete_managed_group],
                "UserPromptSubmit": [unrelated_group],
            }
        }
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(config), encoding="utf-8")

        self.run_registry("uninstall")

        self.assertEqual(
            json.loads(self.config.read_text(encoding="utf-8")),
            {
                "hooks": {
                    "PreCompact": [unrelated_group],
                    "UserPromptSubmit": [unrelated_group],
                }
            },
        )

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from typing import NotRequired, TypedDict, cast
import unittest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "office_hook_registry.py"


class RegistryResult(TypedDict):
    action: str
    config: str
    pluginData: NotRequired[str]
    activation: NotRequired[str]
    activated: NotRequired[bool]
    activationChanged: NotRequired[bool]


class ActivationHook(TypedDict):
    type: str
    command: str
    timeout: int


class ActivationHookGroup(TypedDict):
    hooks: list[ActivationHook]


class ActivationHooks(TypedDict):
    UserPromptSubmit: list[ActivationHookGroup]


class ActivationInputs(TypedDict):
    hooks: ActivationHooks
    unrelated: dict[str, bool]


class HookActivationCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.config = self.base / "codex" / "hooks.json"
        self.codex_config = self.base / "codex" / "config.toml"
        self.data_root = self.base / "plugin data"
        self.data_root.mkdir(parents=True)
        self.last_completed: subprocess.CompletedProcess[str] | None = None

    def run_registry(
        self,
        action: str,
        expected_returncode: int = 0,
        activate: bool = False,
        codex_config: Path | None = None,
    ) -> RegistryResult:
        command = [
            sys.executable,
            os.fspath(REGISTRY),
            action,
            "--config",
            os.fspath(self.config),
            "--data-root",
            os.fspath(self.data_root),
        ]
        if action == "install":
            command.extend(["--plugin-root", os.fspath(ROOT)])
        if activate:
            command.append("--activate")
        if codex_config is not None:
            command.extend(["--codex-config", os.fspath(codex_config)])
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.last_completed = completed
        self.assertEqual(
            completed.returncode,
            expected_returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        if expected_returncode != 0:
            return cast(RegistryResult, {})
        return cast(RegistryResult, json.loads(completed.stdout))

    def expected_hash(self, event: str, group: dict, handler: dict) -> str:
        command_key = "commandWindows" if os.name == "nt" else "command"
        normalized = {
            "type": handler["type"],
            "command": handler[command_key],
            "timeout": max(handler.get("timeout", 600), 1),
            "async": False,
        }
        if "statusMessage" in handler:
            normalized["statusMessage"] = handler["statusMessage"]
        identity = {
            "event_name": {
                "SessionStart": "session_start",
                "UserPromptSubmit": "user_prompt_submit",
                "PreToolUse": "pre_tool_use",
                "PermissionRequest": "permission_request",
                "PostToolUse": "post_tool_use",
                "Stop": "stop",
            }[event],
            "hooks": [normalized],
        }
        if isinstance(group.get("matcher"), str):
            identity["matcher"] = group["matcher"]
        payload = json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def seed_activation_inputs(self) -> ActivationInputs:
        unrelated: ActivationInputs = {
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
        self.codex_config.write_text(
            '[features]\nhooks = false\nkeep = "yes"\n\n'
            '[unrelated]\nvalue = 7\n\n'
            '[hooks.state."unrelated"]\ntrusted_hash = "sha256:keep"\n',
            encoding="utf-8",
        )
        return unrelated

    def test_activate_writes_exact_trust_states_and_is_bounded(self) -> None:
        self.seed_activation_inputs()
        first = self.run_registry(
            "install", activate=True, codex_config=self.codex_config
        )
        self.assertEqual(first["activation"], "activated")
        self.assertTrue(first["activated"])
        installed = json.loads(self.config.read_text(encoding="utf-8"))
        toml_config = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertTrue(toml_config["features"]["hooks"])
        self.assertEqual(toml_config["features"]["keep"], "yes")
        self.assertEqual(toml_config["unrelated"], {"value": 7})
        states = toml_config["hooks"]["state"]
        self.assertEqual(states["unrelated"], {"trusted_hash": "sha256:keep"})

        expected_states = {}
        labels = {
            "SessionStart": "session_start",
            "UserPromptSubmit": "user_prompt_submit",
            "PreToolUse": "pre_tool_use",
            "PermissionRequest": "permission_request",
            "PostToolUse": "post_tool_use",
            "Stop": "stop",
        }
        for event, label in labels.items():
            for group_index, group in enumerate(installed["hooks"][event]):
                for handler_index, handler in enumerate(group["hooks"]):
                    if "OFFICE_OS_MANAGED_HOOK=1" in handler["command"]:
                        key = f"{self.config.absolute()}:{label}:{group_index}:{handler_index}"
                        expected_states[key] = self.expected_hash(event, group, handler)
        self.assertEqual(len(expected_states), 6)
        self.assertEqual(
            {key for key in states if key != "unrelated"}, set(expected_states)
        )
        for key, trusted_hash in expected_states.items():
            self.assertEqual(states[key]["trusted_hash"], trusted_hash)

        activation_state = self.data_root / ".office-os-hook-activation.json"
        first_toml = self.codex_config.read_bytes()
        first_state = activation_state.read_bytes()
        self.assertEqual(len(json.loads(first_state)["states"]), 6)
        second = self.run_registry(
            "install", activate=True, codex_config=self.codex_config
        )
        self.assertFalse(second["activationChanged"])
        self.assertEqual(self.codex_config.read_bytes(), first_toml)
        self.assertEqual(activation_state.read_bytes(), first_state)

    def test_activate_uninstall_removes_only_owned_states_and_is_idempotent(self) -> None:
        unrelated = self.seed_activation_inputs()
        self.run_registry("install", activate=True, codex_config=self.codex_config)
        removed = self.run_registry(
            "uninstall", activate=True, codex_config=self.codex_config
        )
        self.assertEqual(removed["activation"], "deactivated")
        self.assertFalse((self.data_root / ".office-os-hook-activation.json").exists())
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), unrelated)
        remaining_toml = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertTrue(remaining_toml["features"]["hooks"])
        self.assertEqual(remaining_toml["features"]["keep"], "yes")
        self.assertEqual(
            remaining_toml["hooks"]["state"],
            {"unrelated": {"trusted_hash": "sha256:keep"}},
        )
        self.run_registry("uninstall", activate=True, codex_config=self.codex_config)
        self.assertEqual(
            tomllib.loads(self.codex_config.read_text(encoding="utf-8")), remaining_toml
        )

    def test_install_without_activate_does_not_mutate_codex_config(self) -> None:
        self.config.parent.mkdir(parents=True)
        self.codex_config.write_text(
            '[features]\nhooks = false\nkeep = "untouched"\n', encoding="utf-8"
        )
        before = self.codex_config.read_bytes()
        result = self.run_registry("install", codex_config=self.codex_config)
        self.assertEqual(result["activation"], "not_requested")
        self.assertEqual(self.codex_config.read_bytes(), before)
        self.assertFalse((self.data_root / ".office-os-hook-activation.json").exists())

    def test_activate_rejects_linked_codex_config(self) -> None:
        self.codex_config.parent.mkdir(parents=True)
        target = self.base / "real-config.toml"
        target.write_text('[features]\nhooks = false\n', encoding="utf-8")
        try:
            self.codex_config.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            try:
                os.link(target, self.codex_config)
            except OSError as hardlink_error:
                self.skipTest(f"link unavailable: {error}; {hardlink_error}")
        self.run_registry(
            "install",
            expected_returncode=2,
            activate=True,
            codex_config=self.codex_config,
        )
        self.assertIsNotNone(self.last_completed)
        self.assertIn("regular", self.last_completed.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), '[features]\nhooks = false\n')
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


DOCTOR = ROOT / "scripts" / "office_hook_doctor.py"
REGISTRY = ROOT / "scripts" / "office_hook_registry.py"


class HookDoctorCase(HookCliFixture):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.base / "codex" / "hooks.json"
        self.codex = self.base / "codex" / "config.toml"

    def activate(self) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.codex.write_text("[features]\nhooks = false\n", encoding="utf-8")
        command = [sys.executable, "-B", os.fspath(REGISTRY), "install", "--config", os.fspath(self.config),
                   "--data-root", os.fspath(self.plugin_data), "--plugin-root", os.fspath(ROOT), "--activate",
                   "--codex-config", os.fspath(self.codex)]
        completed = self.run_json_command(command)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def run_json_command(self, command: list[str]) -> object:
        import subprocess
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        return subprocess.run(command, text=True, encoding="utf-8", capture_output=True, cwd=self.workspace, env=environment, check=False)

    def check(self, *, record: bool = False, plugin_root: Path = ROOT) -> object:
        command = [sys.executable, "-B", os.fspath(DOCTOR), "check", "--plugin-root", os.fspath(plugin_root),
                   "--data-root", os.fspath(self.plugin_data), "--hooks-config", os.fspath(self.config),
                   "--codex-config", os.fspath(self.codex)]
        if record:
            command.append("--record-latest")
        return self.run_json_command(command)

    def test_good_activation_and_invalid_trust_shape_are_compact(self) -> None:
        self.activate()
        good = self.check()
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout, '{"ok":true,"code":"ok"}\n')
        self.assertEqual(good.stderr, "")

        self.codex.write_text('[features]\nhooks = true\n\n[hooks]\nstate = "invalid"\n', encoding="utf-8")
        bad = self.check()
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, '{"ok":false,"code":"config_trust","remediation":"repair_trust"}\n')
        self.assertEqual(bad.stderr, "")
        self.assertNotIn(os.fspath(self.codex), bad.stdout)

    def test_check_is_read_only_until_record_latest_is_requested(self) -> None:
        self.activate()
        receipt = self.plugin_data / "latest_hook_diagnostic.json"
        receipt.write_text(json.dumps({"version": 1, "created_at": 1, "expires_at": 2,
            "component": "tool_outcome", "event": "PostToolUse", "code": "process_timeout",
            "outcome": "failed", "remediation": "retry_after_timeout"}), encoding="utf-8")
        self.assertEqual(self.check().returncode, 0)
        self.assertTrue(receipt.exists())
        self.assertEqual(self.check(record=True).returncode, 0)
        self.assertFalse(receipt.exists())

    def test_missing_trust_mismatched_root_and_record_latest_are_exact(self) -> None:
        self.activate()
        activation = self.plugin_data / ".office-os-hook-activation.json"
        activation.unlink()
        missing = self.check()
        expected = '{"ok":false,"code":"config_trust","remediation":"repair_trust"}\n'
        self.assertEqual((missing.returncode, missing.stdout, missing.stderr), (2, expected, ""))

        self.activate()
        mismatch = self.check(plugin_root=self.base / "other-root")
        self.assertEqual((mismatch.returncode, mismatch.stdout, mismatch.stderr), (2, expected, ""))
        recorded = self.check(record=True, plugin_root=self.base / "other-root")
        self.assertEqual((recorded.returncode, recorded.stdout, recorded.stderr), (2, expected, ""))
        self.assertEqual(json.loads((self.plugin_data / "latest_hook_diagnostic.json").read_text(encoding="utf-8"))["code"], "config_trust")

    def test_linked_data_root_is_rejected_with_compact_state_safety_json(self) -> None:
        target = self.base / "real-data"
        target.mkdir()
        self.create_directory_link(self.plugin_data, target)

        failed = self.check()

        self.assertEqual((failed.returncode, failed.stdout, failed.stderr), (2, '{"ok":false,"code":"state_safety","remediation":"repair_private_state"}\n', ""))


if __name__ == "__main__":
    unittest.main()

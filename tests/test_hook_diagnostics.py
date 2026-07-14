from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTCOME_HOOK = ROOT / "hooks" / "tool_outcome_hook.py"
DOCTOR = ROOT / "scripts" / "office_hook_doctor.py"
REGISTRY = ROOT / "scripts" / "office_hook_registry.py"
RECEIPT_NAME = "latest_hook_diagnostic.json"
CORE_SCRIPT = ROOT / "skills" / "office-os" / "scripts" / "office_os.py"
MANAGER_SCRIPT = ROOT / "scripts" / "officecli_manager.py"


class HookDiagnosticsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.data_root = self.base / "plugin-data"
        self.hooks_config = self.base / "codex" / "hooks.json"
        self.codex_config = self.base / "codex" / "config.toml"

    def run_process(
        self, command: list[str], *, payload: dict[str, Any] | None = None,
        data_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if data_root is None:
            environment.pop("PLUGIN_DATA", None)
        else:
            environment["PLUGIN_DATA"] = os.fspath(data_root)
        return subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=self.workspace,
            env=environment,
            check=False,
        )

    def post_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__officecli__officecli",
            "tool_input": {"command": ["validate", "candidate.xlsx"]},
            "tool_response": response,
            "cwd": os.fspath(self.workspace),
        }

    def post_bash_payload(
        self, response: dict[str, Any], script: Path = CORE_SCRIPT
    ) -> dict[str, Any]:
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f'python "{script}" status'},
            "tool_response": response,
            "cwd": os.fspath(self.workspace),
        }

    def run_outcome(
        self, payload: dict[str, Any], *, data_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_process(
            [sys.executable, "-B", os.fspath(OUTCOME_HOOK)],
            payload=payload,
            data_root=data_root,
        )

    def receipt(self) -> Path:
        return self.data_root / RECEIPT_NAME

    def write_expired_receipt(self) -> None:
        self.data_root.mkdir()
        self.receipt().write_text(
            json.dumps(
                {
                    "version": 1,
                    "created_at": 1,
                    "expires_at": 2,
                    "component": "tool_outcome",
                    "event": "PostToolUse",
                    "code": "process_timeout",
                    "outcome": "failed",
                    "remediation": "check_runtime",
                }
            ),
            encoding="utf-8",
        )

    def prepare_activation(self) -> None:
        self.hooks_config.parent.mkdir(parents=True, exist_ok=True)
        self.codex_config.write_text("[features]\nhooks = false\n", encoding="utf-8")
        completed = self.run_process(
            [
                sys.executable,
                "-B",
                os.fspath(REGISTRY),
                "install",
                "--config",
                os.fspath(self.hooks_config),
                "--data-root",
                os.fspath(self.data_root),
                "--plugin-root",
                os.fspath(ROOT),
                "--activate",
                "--codex-config",
                os.fspath(self.codex_config),
            ],
            data_root=self.data_root,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def run_doctor(
        self, *, record_latest: bool = False, plugin_root: Path = ROOT
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            "-B",
            os.fspath(DOCTOR),
            "check",
            "--plugin-root",
            os.fspath(plugin_root),
            "--data-root",
            os.fspath(self.data_root),
            "--hooks-config",
            os.fspath(self.hooks_config),
            "--codex-config",
            os.fspath(self.codex_config),
        ]
        if record_latest:
            command.append("--record-latest")
        return self.run_process(command, data_root=self.data_root)

    def test_unrelated_or_successful_post_tool_events_noop_without_receipt(self) -> None:
        unrelated = self.run_outcome(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
            }
        )
        malformed = self.run_outcome(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__officecli__officecli",
                "tool_input": {"command": "not-an-array"},
            }
        )
        successful = self.run_outcome(
            self.post_payload({"isError": False, "content": [{"text": "ok"}]}),
            data_root=self.data_root,
        )

        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        self.assertEqual(unrelated.stdout, "{}\n")
        self.assertEqual(malformed.returncode, 0, malformed.stderr)
        self.assertEqual(malformed.stdout, "{}\n")
        self.assertFalse(self.data_root.exists())
        self.assertEqual(successful.returncode, 0, successful.stderr)
        self.assertEqual(successful.stdout, "{}\n")
        self.assertFalse(self.receipt().exists())

    def test_two_controlled_failures_replace_one_redacted_receipt(self) -> None:
        raw_prompt = "raw-prompt-sentinel"
        office_content = "office-content-sentinel"
        raw_path = os.fspath(self.workspace / "absolute-path-sentinel.xlsx")
        raw_command = f"validate {raw_path}"
        credential = "credential-sentinel"
        user = "user-sentinel"
        pid = "pid-998877"
        full_response = "full-response-sentinel"
        first_payload = self.post_payload(
            {
                "isError": True,
                "content": [{"text": f"OfficeCLI command timed out {office_content} {full_response} {credential} {user} {pid}"}],
            }
        )
        first_payload["prompt"] = raw_prompt
        first_payload["tool_input"] = {"command": ["validate", raw_command]}
        first = self.run_outcome(
            first_payload,
            data_root=self.data_root,
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        first_surfaces = first.stdout + first.stderr + self.receipt().read_text(encoding="utf-8")
        for sentinel in (
            raw_prompt, office_content, raw_path, raw_command, full_response,
            credential, user, pid,
        ):
            self.assertNotIn(sentinel, first_surfaces)
        second = self.run_outcome(
            self.post_payload({"isError": True, "content": [{"text": "managed runtime checksum mismatch"}]}),
            data_root=self.data_root,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        value = json.loads(self.receipt().read_text(encoding="utf-8"))
        self.assertEqual(
            set(value),
            {
                "version", "created_at", "expires_at", "component", "event",
                "code", "outcome", "remediation", "workspace_key",
            },
        )
        self.assertEqual(value["version"], 1)
        self.assertEqual(value["event"], "PostToolUse")
        self.assertEqual(value["code"], "runtime_integrity")
        self.assertEqual(value["workspace_key"], hashlib.sha256(os.path.normcase(os.path.realpath(os.path.abspath(self.workspace))).encode("utf-8")).hexdigest())
        self.assertEqual(first.stderr, "")
        self.assertEqual(second.stderr, "")

    def test_unclassified_error_noops_without_state_or_receipt(self) -> None:
        raw_sentinel = "unknown-error-raw-sentinel"
        completed = self.run_outcome(
            self.post_payload(
                {"isError": True, "content": [{"text": raw_sentinel}]}
            ),
            data_root=self.data_root,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "{}\n")
        self.assertEqual(completed.stderr, "")
        self.assertFalse(self.data_root.exists())

    def test_unclassified_error_leaves_existing_diagnostic_state_untouched(self) -> None:
        self.write_expired_receipt()
        stale = self.data_root / ".office-os-diagnostic.sentinel.tmp"
        stale.write_text("sentinel", encoding="utf-8")
        os.utime(stale, (0, 0))
        receipt_before = self.receipt().read_bytes()

        completed = self.run_outcome(
            self.post_payload(
                {"isError": True, "content": [{"text": "unknown-error-raw-sentinel"}]}
            ),
            data_root=self.data_root,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "{}\n")
        self.assertEqual(completed.stderr, "")
        self.assertEqual(self.receipt().read_bytes(), receipt_before)
        self.assertEqual(stale.read_text(encoding="utf-8"), "sentinel")

    def test_recognized_bounded_bash_failure_writes_one_approved_receipt(self) -> None:
        for script in (CORE_SCRIPT, MANAGER_SCRIPT):
            with self.subTest(script=script.name):
                completed = self.run_outcome(
                    self.post_bash_payload(
                        {"isError": True, "content": [{"text": "OfficeCLI command timed out"}]},
                        script,
                    ),
                    data_root=self.data_root,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("process_timeout", completed.stdout)
        self.assertEqual(json.loads(self.receipt().read_text(encoding="utf-8"))["code"], "process_timeout")

    def test_bounded_response_scan_retains_an_early_known_marker(self) -> None:
        response = {
            "isError": True,
            "content": [{"text": "OfficeCLI command timed out"}]
            + [{"text": "ignored-response-sentinel" * 1024} for _ in range(64)],
        }

        completed = self.run_outcome(self.post_payload(response), data_root=self.data_root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("process_timeout", completed.stdout)
        self.assertNotIn("ignored-response-sentinel", self.receipt().read_text(encoding="utf-8"))

    def test_bounded_response_scan_ignores_late_marker_after_nontext_window(self) -> None:
        response = {
            "isError": True,
            "content": [None] * 9 + [{"text": "OfficeCLI command timed out"}],
        }

        completed = self.run_outcome(self.post_payload(response), data_root=self.data_root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "{}\n")
        self.assertEqual(completed.stderr, "")
        self.assertFalse(self.data_root.exists())

    def test_recognized_failure_cleans_stale_private_temps(self) -> None:
        self.data_root.mkdir()
        stale = self.data_root / ".office-os-diagnostic.stale.tmp"
        fresh = self.data_root / ".office-os-diagnostic.fresh.tmp"
        stale.write_text("stale", encoding="utf-8")
        fresh.write_text("fresh", encoding="utf-8")
        os.utime(stale, (0, 0))

        completed = self.run_outcome(
            self.post_payload({"isError": True, "content": [{"text": "command timed out"}]}),
            data_root=self.data_root,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(self.receipt().exists())

    def test_successful_post_tool_event_cleans_expired_receipt_without_workspace_state(self) -> None:
        self.write_expired_receipt()
        stale = self.data_root / ".office-os-diagnostic.stale.tmp"
        fresh = self.data_root / ".office-os-diagnostic.fresh.tmp"
        stale.write_text("stale", encoding="utf-8")
        fresh.write_text("fresh", encoding="utf-8")
        os.utime(stale, (0, 0))

        completed = self.run_outcome(
            self.post_payload({"isError": False}), data_root=self.data_root
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "{}\n")
        self.assertEqual(completed.stderr, "")
        self.assertFalse(self.receipt().exists())
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertFalse((self.data_root / "workspaces").exists())

    def test_linked_or_hardlinked_receipt_fails_closed_without_touching_sentinel(self) -> None:
        self.data_root.mkdir()
        sentinel = self.base / "outside-receipt.json"
        sentinel.write_text('{"sentinel":true}', encoding="utf-8")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        os.link(sentinel, self.receipt())

        completed = self.run_outcome(
            self.post_payload({"isError": True, "content": [{"text": "command timed out"}]}),
            data_root=self.data_root,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_linked_receipt_fails_closed_without_touching_sentinel(self) -> None:
        self.data_root.mkdir()
        outside = self.base / "outside-receipt"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(self.receipt()), os.fspath(outside)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
            self.addCleanup(os.rmdir, self.receipt())
        else:
            self.receipt().symlink_to(outside, target_is_directory=True)
            self.addCleanup(self.receipt().unlink)

        completed = self.run_outcome(
            self.post_payload({"isError": True, "content": [{"text": "command timed out"}]}),
            data_root=self.data_root,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_doctor_reports_good_missing_trust_mismatched_root_and_record_only_persistence(self) -> None:
        self.prepare_activation()
        good = self.run_doctor()
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout, '{"ok":true,"code":"ok"}\n')
        self.assertEqual(good.stderr, "")
        self.assertNotIn(os.fspath(ROOT), good.stdout)
        self.assertFalse(self.receipt().exists())

        activation = self.data_root / ".office-os-hook-activation.json"
        activation.unlink()
        missing = self.run_doctor()
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, '{"ok":false,"code":"config_trust","remediation":"repair_trust"}\n')
        self.assertEqual(missing.stderr, "")
        self.assertFalse(self.receipt().exists())

        self.prepare_activation()
        mismatch = self.run_doctor(plugin_root=self.base / "other-plugin-root")
        self.assertEqual(mismatch.returncode, 2)
        self.assertEqual(
            mismatch.stdout,
            '{"ok":false,"code":"config_trust","remediation":"repair_trust"}\n',
        )
        self.assertEqual(mismatch.stderr, "")
        self.assertFalse(self.receipt().exists())

        recorded = self.run_doctor(
            record_latest=True, plugin_root=self.base / "other-plugin-root"
        )
        self.assertEqual(recorded.returncode, 2)
        self.assertEqual(
            recorded.stdout,
            '{"ok":false,"code":"config_trust","remediation":"repair_trust"}\n',
        )
        self.assertEqual(recorded.stderr, "")
        self.assertEqual(json.loads(self.receipt().read_text(encoding="utf-8"))["code"], "config_trust")

    def test_doctor_refuses_linked_data_root(self) -> None:
        target = self.base / "real-data"
        target.mkdir()
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(self.data_root), os.fspath(target)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
            self.addCleanup(os.rmdir, self.data_root)
        else:
            self.data_root.symlink_to(target, target_is_directory=True)
            self.addCleanup(self.data_root.unlink)

        completed = self.run_doctor()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stdout,
            '{"ok":false,"code":"state_safety","remediation":"repair_private_state"}\n',
        )
        self.assertEqual(completed.stderr, "")

    def test_doctor_redacts_valid_toml_with_invalid_trust_shape(self) -> None:
        self.prepare_activation()
        self.codex_config.write_text(
            '[features]\nhooks = true\n\n[hooks]\nstate = "invalid"\n',
            encoding="utf-8",
        )

        completed = self.run_doctor()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stdout,
            '{"ok":false,"code":"config_trust","remediation":"repair_trust"}\n',
        )
        self.assertEqual(completed.stderr, "")
        self.assertNotIn(os.fspath(self.codex_config), completed.stdout)

    def test_doctor_check_is_read_only_until_record_latest_is_explicit(self) -> None:
        self.prepare_activation()
        self.receipt().write_text(
            json.dumps(
                {
                    "version": 1,
                    "created_at": 1,
                    "expires_at": 2,
                    "component": "tool_outcome",
                    "event": "PostToolUse",
                    "code": "process_timeout",
                    "outcome": "failed",
                    "remediation": "retry_after_timeout",
                }
            ),
            encoding="utf-8",
        )

        checked = self.run_doctor()
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertTrue(self.receipt().exists())
        recorded = self.run_doctor(record_latest=True)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertFalse(self.receipt().exists())


if __name__ == "__main__":
    unittest.main()

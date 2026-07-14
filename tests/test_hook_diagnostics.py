from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


OUTCOME = ROOT / "hooks" / "tool_outcome_hook.py"
RECEIPT = "latest_hook_diagnostic.json"
CORE = ROOT / "skills" / "office-os" / "scripts" / "office_os.py"
MANAGER = ROOT / "scripts" / "officecli_manager.py"


class ToolOutcomeReceiptCase(HookCliFixture):
    def payload(self, response: dict[str, object]) -> dict[str, object]:
        return {"hook_event_name": "PostToolUse", "tool_name": "mcp__officecli__officecli",
                "tool_input": {"command": ["validate", "candidate.xlsx"]}, "tool_response": response,
                "cwd": os.fspath(self.workspace)}

    def invoke(self, payload: dict[str, object], *, data: bool = True) -> dict[str, object]:
        completed = self.run_json(OUTCOME, payload, data_root=self.plugin_data if data else None)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def receipt(self) -> Path:
        return self.plugin_data / RECEIPT

    def raw(self, payload: dict[str, object]):
        return self.run_json(OUTCOME, payload, data_root=self.plugin_data)

    def expired_receipt(self) -> None:
        self.plugin_data.mkdir(exist_ok=True)
        self.receipt().write_text(json.dumps({"version": 1, "created_at": 1, "expires_at": 2,
            "component": "tool_outcome", "event": "PostToolUse", "code": "process_timeout",
            "outcome": "failed", "remediation": "retry_after_timeout"}), encoding="utf-8")

    def test_unrelated_malformed_and_success_noop_before_diagnostic_state(self) -> None:
        for payload in ({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "git status"}},
                        {"hook_event_name": "PostToolUse", "tool_name": "mcp__officecli__officecli", "tool_input": {"command": "bad"}}):
            with self.subTest(payload=payload):
                self.assertEqual(self.invoke(payload, data=False), {})
        self.assertEqual(self.invoke(self.payload({"isError": False, "content": [{"text": "ok"}]})), {})
        self.assertFalse(self.plugin_data.exists())

    def test_controlled_failure_replaces_one_redacted_receipt(self) -> None:
        prompt, content = "raw-prompt-sentinel", "office-content-sentinel"
        path = os.fspath(self.workspace / "absolute-path-sentinel.xlsx")
        command, response, credential, user, pid = f"validate {path}", "full-response-sentinel", "credential-sentinel", "user-sentinel", "pid-998877"
        failed = self.payload({"isError": True, "content": [{"text": f"OfficeCLI command timed out {content} {response} {credential} {user} {pid}"}]})
        failed["prompt"], failed["tool_input"] = prompt, {"command": ["validate", command]}
        completed = self.raw(failed)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("hookSpecificOutput", json.loads(completed.stdout))
        self.assertTrue(self.receipt().is_file())
        surfaces = completed.stdout + completed.stderr + self.receipt().read_text(encoding="utf-8")
        for value in (prompt, content, path, command, response, credential, user, pid):
            self.assertNotIn(value, surfaces)

        self.assertIn("hookSpecificOutput", self.invoke(self.payload({"isError": True, "content": [{"text": "managed runtime checksum mismatch"}]})))
        value = json.loads(self.receipt().read_text(encoding="utf-8"))
        self.assertEqual(value["code"], "runtime_integrity")
        self.assertEqual(set(value), {"version", "created_at", "expires_at", "component", "event", "code", "outcome", "remediation", "workspace_key"})

    def test_bounded_bash_failure_writes_known_receipt_but_unclassified_does_not(self) -> None:
        for script in (CORE, MANAGER):
            bash = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                    "tool_input": {"command": f'python "{script}" status'},
                    "tool_response": {"is_error": True, "content": [{"text": "process timeout"}]},
                    "cwd": os.fspath(self.workspace)}
            self.assertIn("hookSpecificOutput", self.invoke(bash))
        self.assertEqual(json.loads(self.receipt().read_text(encoding="utf-8"))["code"], "process_timeout")
        before = self.receipt().read_bytes()
        self.assertEqual(self.invoke(self.payload({"isError": True, "content": [{"text": "unclassified"}]})), {})
        self.assertEqual(self.receipt().read_bytes(), before)

    def test_success_cleans_only_expired_receipt(self) -> None:
        self.expired_receipt()
        stale, fresh = self.plugin_data / ".office-os-diagnostic.stale.tmp", self.plugin_data / ".office-os-diagnostic.fresh.tmp"
        stale.write_text("stale", encoding="utf-8")
        fresh.write_text("fresh", encoding="utf-8")
        os.utime(stale, (0, 0))
        self.assertEqual(self.invoke(self.payload({"isError": False, "content": [{"text": "ok"}]})), {})
        self.assertFalse(self.receipt().exists())
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertFalse((self.plugin_data / "workspaces").exists())

    def test_unclassified_failure_leaves_existing_receipt_and_stale_temp_unchanged(self) -> None:
        self.expired_receipt()
        stale = self.plugin_data / ".office-os-diagnostic.sentinel.tmp"
        stale.write_text("sentinel", encoding="utf-8")
        os.utime(stale, (0, 0))
        before = self.receipt().read_bytes()

        completed = self.raw(self.payload({"isError": True, "content": [{"text": "unknown-error-raw-sentinel"}]}))

        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, "{}\n", ""))
        self.assertEqual(self.receipt().read_bytes(), before)
        self.assertEqual(stale.read_text(encoding="utf-8"), "sentinel")

    def test_bounded_scan_keeps_early_known_marker(self) -> None:
        early = self.payload({"isError": True, "content": [{"text": "OfficeCLI command timed out"}]
            + [{"text": "ignored-sentinel" * 1024} for _ in range(64)]})
        self.assertIn("hookSpecificOutput", self.invoke(early))
        self.assertEqual(json.loads(self.receipt().read_text(encoding="utf-8"))["code"], "process_timeout")
        self.assertNotIn("ignored-sentinel", self.receipt().read_text(encoding="utf-8"))

    def test_bounded_scan_ignores_late_marker_after_nontext_window(self) -> None:
        late = self.raw(self.payload({"isError": True, "content": [None] * 9 + [{"text": "OfficeCLI command timed out"}]}))
        self.assertEqual((late.returncode, late.stdout, late.stderr), (0, "{}\n", ""))
        self.assertFalse(self.plugin_data.exists())

    def test_known_failure_cleans_stale_private_temp_only(self) -> None:
        self.plugin_data.mkdir()
        stale, fresh = self.plugin_data / ".office-os-diagnostic.stale.tmp", self.plugin_data / ".office-os-diagnostic.fresh.tmp"
        stale.write_text("stale", encoding="utf-8")
        fresh.write_text("fresh", encoding="utf-8")
        os.utime(stale, (0, 0))

        self.assertIn("hookSpecificOutput", self.invoke(self.payload({"isError": True, "content": [{"text": "command timed out"}]})))

        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_linked_and_hardlinked_receipts_fail_closed_without_touching_sentinel(self) -> None:
        self.plugin_data.mkdir()
        sentinel = self.base / "receipt-sentinel.json"
        sentinel.write_text('{"sentinel":true}', encoding="utf-8")
        os.link(sentinel, self.receipt())
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        failed = self.raw(self.payload({"isError": True, "content": [{"text": "command timed out"}]}))
        self.assertEqual((failed.returncode, failed.stdout), (1, ""))
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

        self.receipt().unlink()
        outside = self.base / "receipt-outside"
        outside.mkdir()
        linked = outside / "sentinel.txt"
        linked.write_text("outside", encoding="utf-8")
        self.create_directory_link(self.receipt(), outside)
        failed = self.raw(self.payload({"isError": True, "content": [{"text": "command timed out"}]}))
        self.assertEqual((failed.returncode, failed.stdout), (1, ""))
        self.assertEqual(linked.read_text(encoding="utf-8"), "outside")


if __name__ == "__main__":
    unittest.main()

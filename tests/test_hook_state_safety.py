from __future__ import annotations

import hashlib
import os
from pathlib import Path
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


INTAKE = ROOT / "hooks" / "intake_router_hook.py"


class HookStateSafetyCase(HookCliFixture):
    def named(self, turn: str = "turn") -> None:
        (self.workspace / "report.xlsx").touch(exist_ok=True)
        completed = self.run_json(INTAKE, self.prompt("Review report.xlsx", turn), data_root=self.plugin_data)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")

    def sentinel_link(self, link: Path) -> Path:
        outside = self.base / f"outside-{link.name}"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        self.create_directory_link(link, outside)
        return sentinel

    def test_linked_plugin_data_and_ancestor_fail_before_named_state_write(self) -> None:
        sentinel = self.sentinel_link(self.plugin_data)
        self.named("linked-root")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")

        self.remove_directory_link(self.plugin_data)
        parent = self.base / "parent"
        parent.mkdir()
        self.create_directory_link(self.base / "linked-parent", parent)
        self.plugin_data = self.base / "linked-parent" / "plugin-data"
        (parent / "plugin-data").mkdir()
        self.named("linked-ancestor")

    def test_linked_plugin_data_ancestor_preserves_external_sentinel_bytes(self) -> None:
        target = self.base / "ancestor-target"
        data = target / "plugin-data"
        data.mkdir(parents=True)
        sentinel = data / "sentinel.bin"
        sentinel.write_bytes(b"external-ancestor-sentinel")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        linked_parent = self.base / "linked-ancestor"
        self.create_directory_link(linked_parent, target)
        self.plugin_data = linked_parent / "plugin-data"
        (self.workspace / "report.xlsx").touch()

        completed = self.run_json(INTAKE, self.prompt("Review report.xlsx", "linked-ancestor"), data_root=self.plugin_data)

        self.assertEqual((completed.returncode, completed.stdout), (1, ""))
        self.assertTrue(completed.stderr)
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
        self.assertEqual((data / "sentinel.bin").read_bytes(), b"external-ancestor-sentinel")

    def test_linked_workspaces_and_workspace_identifier_fail_closed(self) -> None:
        self.plugin_data.mkdir()
        sentinel = self.sentinel_link(self.plugin_data / "workspaces")
        self.named("linked-workspaces")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")

        self.remove_directory_link(self.plugin_data / "workspaces")
        (self.plugin_data / "workspaces").mkdir()
        identifier = hashlib.sha256(os.path.normcase(os.path.realpath(os.path.abspath(self.workspace))).encode()).hexdigest()[:24]
        sentinel = self.sentinel_link(self.plugin_data / "workspaces" / identifier)
        self.named("linked-workspace")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")

    def test_hardlinked_dedup_and_root_lock_leave_external_sentinel_unchanged(self) -> None:
        directory = self.workspace_data()
        sentinel = self.base / "dedup-sentinel"
        sentinel.write_text("outside", encoding="utf-8")
        os.link(sentinel, directory / "hook_dedup.json")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        self.named("hardlinked-dedup")
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

        (directory / "hook_dedup.json").unlink()
        lock = self.base / "lock-sentinel"
        lock.write_text("outside", encoding="utf-8")
        os.link(lock, self.plugin_data / "run-state.lock")
        before = hashlib.sha256(lock.read_bytes()).hexdigest()
        completed = self.run_json(INTAKE, self.prompt("Schedule an Excel spreadsheet"), data_root=self.plugin_data)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(hashlib.sha256(lock.read_bytes()).hexdigest(), before)

    def test_hardlinked_pending_intake_fails_before_access(self) -> None:
        self.plugin_data.mkdir()
        pending = self.base / "pending-sentinel"
        pending.write_text('{"entries":[]}', encoding="utf-8")
        os.link(pending, self.plugin_data / "pending_intakes.json")
        before = hashlib.sha256(pending.read_bytes()).hexdigest()
        completed = self.run_json(INTAKE, self.prompt("Schedule an Excel spreadsheet"), data_root=self.plugin_data)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(hashlib.sha256(pending.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

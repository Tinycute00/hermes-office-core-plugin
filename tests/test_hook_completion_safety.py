from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


COMPLETION = ROOT / "hooks" / "completion_hook.py"
INTAKE = ROOT / "hooks" / "intake_router_hook.py"


class CompletionSafetyCase(HookCliFixture):
    def seed(self) -> None:
        completed = self.run_json(INTAKE, self.prompt("Schedule an Excel spreadsheet"), data_root=self.plugin_data)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def stop(self) -> dict[str, object]:
        return {"hook_event_name": "Stop", "session_id": "session-1", "turn_id": "turn-1",
                "cwd": os.fspath(self.workspace), "stop_hook_active": False}

    def fail_closed(self) -> None:
        completed = self.run_json(COMPLETION, self.stop(), data_root=self.plugin_data)
        self.assertEqual((completed.returncode, completed.stdout), (1, ""))

    def test_linked_pending_intake_refuses_stop_before_external_read(self) -> None:
        self.seed()
        pending = self.plugin_data / "pending_intakes.json"
        pending.unlink()
        outside = self.base / "outside-pending"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        self.create_directory_link(pending, outside)

        self.fail_closed()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")

    def test_hardlinked_pending_intake_refuses_stop_before_external_read(self) -> None:
        self.seed()
        pending = self.plugin_data / "pending_intakes.json"
        contents = pending.read_bytes()
        pending.unlink()
        sentinel = self.base / "pending-sentinel.json"
        sentinel.write_bytes(contents)
        os.link(sentinel, pending)
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()

        self.fail_closed()

        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_hardlinked_workspace_lock_refuses_stop_before_external_write(self) -> None:
        directory = self.workspace_data()
        (directory / "run_state.json").write_text(json.dumps({"status": "executing", "remaining_units": 1,
            "waiting_for_user": False, "progress_marker": "chunk-1", "last_stop_marker": ""}), encoding="utf-8")
        sentinel = self.base / "lock-sentinel.json"
        sentinel.write_text("outside", encoding="utf-8")
        os.link(sentinel, directory / "run-state.lock")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()

        self.fail_closed()

        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

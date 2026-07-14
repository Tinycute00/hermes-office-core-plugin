from __future__ import annotations

import json
import os
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


ENTRYPOINT = ROOT / "hooks" / "intake_router_hook.py"


class IntakeStateCase(HookCliFixture):
    def invoke(self, prompt: str, turn: str = "turn-1", session: str = "session-1") -> None:
        completed = self.run_json(ENTRYPOINT, self.prompt(prompt, turn, session), data_root=self.plugin_data)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_same_session_replaces_marker_but_other_session_preserves_it(self) -> None:
        self.invoke("Schedule an Excel spreadsheet", "old", "owner")
        old = self.pending_entries()[0]
        old_key = old["key"]
        self.invoke("Review a Word document", "new", "owner")
        entries = self.pending_entries()
        self.assertEqual(len(entries), 1)
        self.assertNotIn(old_key, {entry["key"] for entry in entries})
        self.assertIn("物件：Word", str(entries[0]["expected"]))

        self.invoke("Schedule a PDF review", "other", "other-session")
        self.assertEqual(len(self.pending_entries()), 2)

    def test_named_source_discards_only_same_session_marker(self) -> None:
        self.invoke("Schedule an Excel spreadsheet", session="owner")
        self.invoke("Schedule a Word document", session="other")
        other_key = self.pending_entries()[1]["key"]
        (self.workspace / "budget.xlsx").touch()

        self.invoke("Review budget.xlsx", "named", "owner")

        entries = self.pending_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], other_key)

    def test_pending_markers_cap_at_128_and_expire_stale_entry(self) -> None:
        for number in range(140):
            self.invoke("Schedule an Excel spreadsheet", f"turn-{number}", f"session-{number}")
        entries = self.pending_entries()
        self.assertEqual(len(entries), 128)
        stale = entries[-1]["key"]
        entries[-1]["created_at"] = 0
        (self.plugin_data / "pending_intakes.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")

        self.invoke("Schedule a PDF review", "fresh", "fresh")

        refreshed = self.pending_entries()
        self.assertEqual(len(refreshed), 128)
        self.assertNotIn(stale, {entry["key"] for entry in refreshed})

    def test_future_marker_expires_before_new_source_free_intake(self) -> None:
        self.invoke("Schedule an Excel spreadsheet", "old", "old")
        entries = self.pending_entries()
        future = entries[0]["key"]
        entries[0]["created_at"] = 2**31
        (self.plugin_data / "pending_intakes.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")

        self.invoke("Schedule a PDF review", "fresh", "fresh")

        self.assertNotIn(future, {entry["key"] for entry in self.pending_entries()})

    def test_correction_prompt_discards_seeded_same_session_marker_without_reintake(self) -> None:
        self.invoke("Schedule an Excel spreadsheet", "source", "same")
        correction = "The source-free Office intake final reply was not canonical. Return exactly these two lines and nothing else."
        self.invoke(correction, "correction", "same")

        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())
        stop = {"hook_event_name": "Stop", "session_id": "same", "turn_id": "correction",
                "cwd": os.fspath(self.workspace), "stop_hook_active": False}
        completed = self.run_json(ROOT / "hooks" / "completion_hook.py", stop, data_root=self.plugin_data)
        self.assertEqual((completed.returncode, completed.stdout), (0, "{}\n"))

    def test_workspace_dedup_caps_at_128_without_raw_prompt_or_path(self) -> None:
        for number in range(140):
            name = f"report-{number}.xlsx"
            (self.workspace / name).touch()
            self.invoke(f"Review {name}", f"turn-{number}")
        workspace = self.workspace_data()
        value = json.loads((workspace / "hook_dedup.json").read_text(encoding="utf-8"))

        self.assertEqual(len(value["keys"]), 128)
        self.assertNotIn("report-139.xlsx", json.dumps(value))
        self.assertNotIn(os.fspath(self.workspace), json.dumps(value))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


COMPLETION = ROOT / "hooks" / "completion_hook.py"
INTAKE = ROOT / "hooks" / "intake_router_hook.py"


class CompletionFlowCase(HookCliFixture):
    def stop(self, *, active: bool = False, session: str = "session-1", turn: str = "turn-1", message: str | None = None) -> dict[str, object]:
        return {"hook_event_name": "Stop", "session_id": session, "turn_id": turn,
                "cwd": os.fspath(self.workspace), "stop_hook_active": active,
                **({"last_assistant_message": message} if message is not None else {})}

    def invoke(self, payload: dict[str, object], *, data: bool = True) -> dict[str, object]:
        completed = self.run_json(COMPLETION, payload, data_root=self.plugin_data if data else None)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def seed_pending(self, session: str = "session-1") -> None:
        completed = self.run_json(INTAKE, self.prompt("Schedule an Excel spreadsheet", session=session), data_root=self.plugin_data)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def active_state(self, marker: str, last: str, count: int) -> Path:
        path = self.workspace_data() / "run_state.json"
        path.write_text(json.dumps({"run_id": "run", "status": "executing", "remaining_units": 3,
            "waiting_for_user": False, "progress_marker": marker, "last_stop_marker": last,
            "continuation_count": count}), encoding="utf-8")
        return path

    def test_unknown_or_missing_event_noops_before_plugin_data(self) -> None:
        for payload in ({"hook_event_name": "SessionStart"}, {"cwd": os.fspath(self.workspace)}):
            self.assertEqual(self.invoke(payload, data=False), {})
        self.assertFalse(self.plugin_data.exists())

    def test_no_pending_source_free_stop_never_guesses_or_creates_state(self) -> None:
        self.assertEqual(self.invoke(self.stop()), {})
        self.assertFalse(self.plugin_data.exists())

    def test_noncanonical_source_free_stop_consumes_marker_once(self) -> None:
        self.seed_pending()
        answer = self.invoke(self.stop())
        self.assertEqual(answer["decision"], "block")
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())
        self.assertEqual(self.invoke(self.stop(active=True)), {})

    def test_canonical_final_and_moved_turn_consume_same_session_marker(self) -> None:
        self.seed_pending()
        expected = str(self.pending_entries()[0]["expected"])
        self.assertEqual(self.invoke(self.stop(message=expected)), {})
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

        self.seed_pending()
        expected = str(self.pending_entries()[0]["expected"])
        self.assertEqual(self.invoke(self.stop(turn="moved", message=expected)), {})
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

    def test_cross_session_stop_preserves_pending_marker(self) -> None:
        self.seed_pending("owner")
        self.assertEqual(self.invoke(self.stop(session="other")), {})
        self.assertTrue((self.plugin_data / "pending_intakes.json").exists())

    def test_second_distinct_progress_allows_final_continuation_before_cap(self) -> None:
        state = self.active_state("chunk-1", "", 0)
        self.assertEqual(self.invoke(self.stop())["decision"], "block")
        value = json.loads(state.read_text(encoding="utf-8"))
        value["progress_marker"] = "chunk-2"
        value["remaining_units"] = 2
        state.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(self.invoke(self.stop())["decision"], "block")
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["continuation_count"], 2)

    def test_no_progress_returns_empty_without_increment_before_second_progression(self) -> None:
        state = self.active_state("chunk-1", "chunk-1", 0)
        self.assertEqual(self.invoke(self.stop()), {})
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["continuation_count"], 0)

    def test_third_continuation_is_capped_and_reentry_is_unchanged(self) -> None:
        state = self.active_state("chunk-3", "chunk-2", 2)
        before = state.read_bytes()
        self.assertEqual(self.invoke(self.stop()), {})
        self.assertEqual(self.invoke(self.stop(active=True)), {})
        self.assertEqual(state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

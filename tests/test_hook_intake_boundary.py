from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


ENTRYPOINT = ROOT / "hooks" / "intake_router_hook.py"


class IntakeBoundaryCase(HookCliFixture):
    def prompt(self, text: str) -> dict[str, str]:
        return {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": os.fspath(self.workspace),
            "prompt": text,
        }

    def test_hardlinked_pending_intake_refuses_without_touching_sentinel(self) -> None:
        self.plugin_data.mkdir()
        sentinel = self.base / "outside-pending.json"
        sentinel.write_text('{"entries":[]}', encoding="utf-8")
        os.link(sentinel, self.plugin_data / "pending_intakes.json")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()

        completed = self.run_json(
            ENTRYPOINT, self.prompt("Schedule an Excel spreadsheet"), data_root=self.plugin_data
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

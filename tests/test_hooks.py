from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "office_hook.py"


class HookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def run_hook(self, payload: dict) -> dict | None:
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        completed = subprocess.run(
            [sys.executable, os.fspath(HOOK)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            cwd=self.workspace,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def workspace_data(self) -> Path:
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace)))
        identifier = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        directory = self.plugin_data / "workspaces" / identifier
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def prompt_payload(self, prompt: str, turn: str = "turn-1") -> dict:  # noqa: DICT_OK
        return {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": turn,
            "cwd": os.fspath(self.workspace),
            "prompt": prompt,
        }

    def test_office_prompt_injects_skill_and_relevant_reference_once(self) -> None:
        prompt = "請更新 " + chr(96) + "budget.xlsx" + chr(96) + "，保留原檔。"
        payload = self.prompt_payload(prompt)
        first = self.run_hook(payload)
        self.assertIsNotNone(first)
        context = first["hookSpecificOutput"]["additionalContext"]
        self.assertIn("$office-os", context)
        self.assertIn("Excel.md", context)
        self.assertIn("意圖：<值>", context)
        self.assertIsNone(self.run_hook(payload))

    def test_single_object_schedule_routes_office_and_object_references(self) -> None:
        result = self.run_hook(
            self.prompt_payload("每週更新 budget.xlsx 並保留排程", "turn-schedule")
        )
        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(context.count("Excel.md"), 1)
        self.assertEqual(context.count("Office.md"), 1)
        self.assertEqual(
            {path.name for path in self.workspace_data().iterdir()},
            {"hook_dedup.json"},
        )

    def test_code_only_and_unrelated_prompts_do_not_trigger(self) -> None:
        fence = chr(96) * 3
        code_prompt = f"{fence}\n請修改 Excel 工作簿\n{fence}"
        self.assertIsNone(self.run_hook(self.prompt_payload(code_prompt)))
        self.assertIsNone(
            self.run_hook(
                self.prompt_payload("How can I make my office chair more comfortable?", "turn-2")
            )
        )

    def test_cross_file_prompt_routes_to_office_map(self) -> None:
        result = self.run_hook(
            self.prompt_payload(
                "比較 finance.xlsx 與 summary.docx，整理差異。", "turn-cross"
            )
        )
        self.assertIn(
            "Office.md", result["hookSpecificOutput"]["additionalContext"]
        )

    def test_session_start_restores_only_compact_active_pointer(self) -> None:
        state = {
            "run_id": "run-1",
            "status": "executing",
            "remaining_units": 4,
            "waiting_for_user": False,
        }
        (self.workspace_data() / "run_state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        result = self.run_hook(
            {
                "hook_event_name": "SessionStart",
                "session_id": "session-1",
                "cwd": os.fspath(self.workspace),
                "source": "compact",
            }
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("run-1", context)
        self.assertIn("remaining_units=4", context)

    def test_stop_continuation_requires_new_progress_and_is_capped_at_two(self) -> None:
        state_path = self.workspace_data() / "run_state.json"
        state = {
            "run_id": "run-stop",
            "status": "executing",
            "remaining_units": 3,
            "waiting_for_user": False,
            "progress_marker": "chunk-1",
            "last_stop_marker": "",
            "continuation_count": 0,
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "session_id": "session-1",
            "turn_id": "turn-stop",
            "cwd": os.fspath(self.workspace),
            "stop_hook_active": False,
        }
        first = self.run_hook(payload)
        self.assertEqual(first["decision"], "block")

        no_progress = self.run_hook(payload)
        self.assertEqual(no_progress, {})

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["progress_marker"] = "chunk-2"
        state["remaining_units"] = 2
        state_path.write_text(json.dumps(state), encoding="utf-8")
        second = self.run_hook(payload)
        self.assertEqual(second["decision"], "block")

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["progress_marker"] = "chunk-3"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        capped = self.run_hook(payload)
        self.assertEqual(capped, {})
        final = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(final["continuation_count"], 2)

    def test_prompt_dedup_is_bounded(self) -> None:
        for number in range(140):
            self.run_hook(
                self.prompt_payload(
                    f"檢查 report-{number}.xlsx", turn=f"turn-{number}"
                )
            )
        data = json.loads(
            (self.workspace_data() / "hook_dedup.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(data["keys"]), 128)


if __name__ == "__main__":
    unittest.main()

# noqa: SIZE_OK - behavioral hook coverage shares one process-level fixture surface.
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

    def run_hook(self, payload: dict, expected_returncode: int = 0) -> dict | None:
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
            expected_returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def create_directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        else:
            link.symlink_to(target, target_is_directory=True)
        self.addCleanup(self.remove_directory_link, link)

    def remove_directory_link(self, link: Path) -> None:
        if not os.path.lexists(link):
            return
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()

    def assert_state_link_rejected(self, turn: str, outside: Path) -> None:
        result = self.run_hook(
            self.prompt_payload("review report.xlsx", turn),
            expected_returncode=1,
        )
        self.assertIsNone(result)
        self.assertEqual({path.name for path in outside.iterdir()}, {"sentinel.txt"})
        self.assertEqual(
            (outside / "sentinel.txt").read_text(encoding="utf-8"),
            "outside",
        )

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
        self.assertIn("Office.md", context)
        self.assertIn("意圖：<值>", context)
        self.assertIn("PLUGIN_DATA", context)
        self.assertIn(os.fspath(self.plugin_data), context)
        self.assertIsNone(self.run_hook(payload))

    def test_office_prompt_requires_one_final_envelope_reply(self) -> None:
        result = self.run_hook(self.prompt_payload("每週更新 budget.xlsx"))
        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        for marker in (
            "exactly one final assistant message",
            "first line must be the intent envelope",
            "exactly one short question after the envelope",
            "same final message",
            "no visible preamble, plan, skill announcement, tool-activity summary, or separate progress message",
        ):
            self.assertIn(marker, context)

    def test_missing_source_intake_classifies_before_office_work(self) -> None:
        result = self.run_hook(
            self.prompt_payload("請用 $office-os 幫我每週更新 Excel 報表；先不要改檔案。")
        )
        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        skill_path = ROOT / "skills" / "office-os" / "SKILL.md"

        self.assertTrue(context.startswith("<office-os-source-free-intake>\n"), context)
        self.assertIn(
            "FIRST USER-VISIBLE RESPONSE MUST BEGIN WITH A COMPACT INTENT CLASSIFICATION:",
            context,
        )
        self.assertIn(
            "After loading this skill, provide this canonical envelope and one source request:",
            context,
        )
        self.assertIn("意圖：排程｜物件：Excel｜權限：唯讀｜檢查：快速", context)
        self.assertIn("Do not inspect or alter Office data", context)
        self.assertIn("Do not call `office_os.py`, OfficeCLI, or an MCP tool", context)
        self.assertIn(
            "Loading this skill to honor an explicit $office-os invocation is allowed", context
        )
        self.assertNotIn(os.fspath(skill_path), context)
        self.assertNotIn("Office.md", context)

    def test_hook_rejects_claude_only_plugin_data(self) -> None:
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment["CLAUDE_PLUGIN_DATA"] = os.fspath(self.plugin_data)
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)

        completed = subprocess.run(
            [sys.executable, os.fspath(HOOK)],
            input=json.dumps(self.prompt_payload("review budget.xlsx")),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            cwd=self.workspace,
            check=False,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("PLUGIN_DATA", completed.stderr)
        self.assertFalse(self.plugin_data.exists())

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
        self.assertIn("PLUGIN_DATA", context)
        self.assertIn(os.fspath(self.plugin_data), context)

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
        (self.workspace_data() / "run-state.lock").write_text(
            json.dumps({"pid": 0, "token": "released-core-lock"}),
            encoding="ascii",
        )
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

    def test_hook_rejects_linked_plugin_data_before_state_write(self) -> None:
        outside = self.base / "outside-plugin-data"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        self.create_directory_link(self.plugin_data, outside)

        self.assert_state_link_rejected("turn-linked-plugin-data", outside)

    def test_hook_rejects_linked_workspaces_root_before_state_write(self) -> None:
        self.plugin_data.mkdir()
        outside = self.base / "outside-workspaces"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        self.create_directory_link(self.plugin_data / "workspaces", outside)

        self.assert_state_link_rejected("turn-linked-workspaces", outside)

    def test_hook_rejects_linked_workspace_id_before_state_write(self) -> None:
        workspaces = self.plugin_data / "workspaces"
        workspaces.mkdir(parents=True)
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace)))
        identifier = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        outside = self.base / "outside-workspace-id"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        self.create_directory_link(workspaces / identifier, outside)

        self.assert_state_link_rejected("turn-linked-workspace-id", outside)

    def test_hook_rejects_linked_plugin_data_ancestor_before_state_write(self) -> None:
        outside = self.base / "outside-ancestor"
        plugin_data = outside / "plugin-data"
        plugin_data.mkdir(parents=True)
        (plugin_data / "sentinel.txt").write_text("outside", encoding="utf-8")
        linked_parent = self.base / "linked-parent"
        self.create_directory_link(linked_parent, outside)
        self.plugin_data = linked_parent / "plugin-data"

        self.assert_state_link_rejected("turn-linked-ancestor", plugin_data)

    def test_hook_rejects_hardlinked_dedup_before_write(self) -> None:
        directory = self.workspace_data()
        sentinel = self.base / "outside-dedup.json"
        sentinel.write_text("outside dedup", encoding="utf-8")
        os.link(sentinel, directory / "hook_dedup.json")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()

        self.assertIsNone(
            self.run_hook(self.prompt_payload("review report.xlsx", "turn-hardlink-dedup"), 1)
        )
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_hook_rejects_hardlinked_lock_before_write(self) -> None:
        directory = self.workspace_data()
        (directory / "run_state.json").write_text(
            json.dumps(
                {
                    "status": "executing",
                    "remaining_units": 1,
                    "waiting_for_user": False,
                    "progress_marker": "chunk-1",
                }
            ),
            encoding="utf-8",
        )
        sentinel = self.base / "outside-lock.json"
        sentinel.write_text("outside lock", encoding="utf-8")
        os.link(sentinel, directory / "run-state.lock")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        payload = {
            "hook_event_name": "Stop",
            "session_id": "session-1",
            "turn_id": "turn-hardlink-lock",
            "cwd": os.fspath(self.workspace),
        }

        self.assertIsNone(self.run_hook(payload, 1))
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_hook_rejects_hardlinked_run_state_before_read(self) -> None:
        directory = self.workspace_data()
        sentinel = self.base / "outside-run-state.json"
        sentinel.write_text('{"status":"executing","remaining_units":1}', encoding="utf-8")
        os.link(sentinel, directory / "run_state.json")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
            "cwd": os.fspath(self.workspace),
        }

        self.assertIsNone(self.run_hook(payload, 1))
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

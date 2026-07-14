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
INTAKE_ROUTER_HOOK = ROOT / "hooks" / "intake_router_hook.py"


class IntakeRouterCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def run_hook(
        self,
        payload: dict[str, str],
        *,
        with_plugin_data: bool = True,
        expected_returncode: int = 0,
    ) -> dict[str, object] | None:
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        if with_plugin_data:
            environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        else:
            environment.pop("PLUGIN_DATA", None)
        completed = subprocess.run(
            [sys.executable, os.fspath(INTAKE_ROUTER_HOOK)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            cwd=self.workspace,
            check=False,
        )
        self.assertEqual(completed.returncode, expected_returncode, msg=f"stdout={completed.stdout}\nstderr={completed.stderr}")
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def prompt_payload(
        self, prompt: str, turn_id: str = "turn-1", session_id: str = "session-1"
    ) -> dict[str, str]:
        return {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": os.fspath(self.workspace),
            "prompt": prompt,
        }

    def pending_entries(self) -> list[dict[str, object]]:
        return json.loads((self.plugin_data / "pending_intakes.json").read_text(encoding="utf-8"))["entries"]

    def create_directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)], text=True, capture_output=True, check=False)
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

    def test_source_free_prompt_emits_exact_two_line_contract(self) -> None:
        result = self.run_hook(
            self.prompt_payload("Schedule an Excel spreadsheet every week without changing files.")
        )

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertIsInstance(output, dict)
        context = output["additionalContext"]
        self.assertIsInstance(context, str)
        self.assertIn("<office-os-source-free-intake>", context)
        self.assertIn(
            "意圖：排程｜物件：Excel｜權限：唯讀｜檢查：快速\n"
            "Excel 來源檔或資料夾路徑是什麼？",
            context,
        )
        self.assertEqual(context.count("來源檔或資料夾路徑是什麼？"), 1)
        self.assertTrue((self.plugin_data / "pending_intakes.json").is_file())
        self.assertFalse((self.plugin_data / "workspaces").exists())

    def test_named_local_source_routes_once_with_excel_references(self) -> None:
        source = self.workspace / "budget.xlsx"
        source.touch()

        result = self.run_hook(self.prompt_payload("Review budget.xlsx"))

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertIsInstance(output, dict)
        context = output["additionalContext"]
        self.assertIsInstance(context, str)
        self.assertTrue(context.startswith("<office-os-intake>\n"), context)
        self.assertEqual(context.count("Office.md"), 1)
        self.assertEqual(context.count("Excel.md"), 1)
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())
        workspace_roots = list((self.plugin_data / "workspaces").iterdir())
        self.assertEqual(len(workspace_roots), 1)
        self.assertTrue((workspace_roots[0] / "hook_dedup.json").is_file())
        self.assertIsNone(self.run_hook(self.prompt_payload("Review budget.xlsx")))

    def test_missing_office_filename_remains_source_free(self) -> None:
        result = self.run_hook(self.prompt_payload("Update missing-budget.xlsx every week"))

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertIsInstance(output, dict)
        context = output["additionalContext"]
        self.assertIsInstance(context, str)
        self.assertIn("<office-os-source-free-intake>", context)
        self.assertEqual(len(self.pending_entries()), 1)
        self.assertFalse((self.plugin_data / "workspaces").exists())

    def test_url_office_filename_remains_source_free(self) -> None:
        result = self.run_hook(
            self.prompt_payload("Review https://example.invalid/budget.xlsx")
        )

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertIsInstance(output, dict)
        context = output["additionalContext"]
        self.assertIsInstance(context, str)
        self.assertIn("<office-os-source-free-intake>", context)
        self.assertEqual(len(self.pending_entries()), 1)
        self.assertFalse((self.plugin_data / "workspaces").exists())

    def test_code_only_and_non_office_prompts_do_not_create_state(self) -> None:
        fence = chr(96) * 3
        code_prompt = f"{fence}\nUpdate an Excel workbook\n{fence}"

        self.assertIsNone(self.run_hook(self.prompt_payload(code_prompt)))
        self.assertIsNone(
            self.run_hook(self.prompt_payload("How can I make my office chair more comfortable?"))
        )
        self.assertFalse(self.plugin_data.exists())

    def test_repository_maintenance_with_office_words_does_not_create_state(self) -> None:
        prompts = (
            "Update the Excel workbook parser in hooks",
            "修正 hooks 裡 Excel 工作簿 parser 的測試",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(self.run_hook(self.prompt_payload(prompt)), {})
                self.assertFalse(self.plugin_data.exists())

    def test_inline_path_repository_maintenance_emits_empty_output_without_state(self) -> None:
        prompt = (
            "Update the Excel workbook parser in "
            f"{chr(96)}hooks/office_hooks/intent.py{chr(96)}"
        )

        self.assertEqual(self.run_hook(self.prompt_payload(prompt)), {})
        self.assertFalse(self.plugin_data.exists())

    def test_chinese_repository_maintenance_emits_empty_output_without_state(self) -> None:
        prompt = "修正專案裡 Excel 工作簿解析器的單元測試"

        self.assertEqual(self.run_hook(self.prompt_payload(prompt)), {})
        self.assertFalse(self.plugin_data.exists())

    def test_office_workbook_tracking_code_terms_remains_source_free(self) -> None:
        result = self.run_hook(
            self.prompt_payload("Create an Excel workbook to track source code unit tests")
        )

        self.assertIsNotNone(result)
        self.assertIn(
            "<office-os-source-free-intake>", result["hookSpecificOutput"]["additionalContext"]
        )
        self.assertTrue((self.plugin_data / "pending_intakes.json").is_file())

    def test_chinese_office_prompt_remains_source_free_intake(self) -> None:
        result = self.run_hook(self.prompt_payload("檢查 Excel 工作簿"))

        self.assertIsNotNone(result)
        self.assertIn(
            "<office-os-source-free-intake>", result["hookSpecificOutput"]["additionalContext"]
        )

    def test_office_prompt_with_unpaired_implementation_word_remains_source_free(self) -> None:
        result = self.run_hook(self.prompt_payload("Check the Excel workbook parser"))

        self.assertIsNotNone(result)
        self.assertIn(
            "<office-os-source-free-intake>", result["hookSpecificOutput"]["additionalContext"]
        )

    def test_repository_maintenance_discards_existing_session_pending_intake(self) -> None:
        prompts = (
            "Update the Excel workbook parser in hooks",
            "修正 hooks 裡 Excel 工作簿 parser 的測試",
        )

        for number, prompt in enumerate(prompts):
            session_id = f"shared-{number}"
            with self.subTest(prompt=prompt):
                self.run_hook(
                    self.prompt_payload("Schedule an Excel spreadsheet", session_id=session_id)
                )
                self.assertEqual(
                    self.run_hook(
                        self.prompt_payload(prompt, "turn-maintenance", session_id)
                    ),
                    {},
                )
                self.assertFalse((self.plugin_data / "pending_intakes.json").exists())
                self.assertFalse((self.plugin_data / "workspaces").exists())

    def test_unrelated_prompts_without_plugin_data_emit_empty_output(self) -> None:
        fence = chr(96) * 3
        prompts = (
            "What is the capital of France?",
            f"{fence}\nUpdate an Excel workbook\n{fence}",
            "Update the Excel workbook parser in hooks",
        )

        for prompt in prompts:
            self.assertEqual(
                self.run_hook(self.prompt_payload(prompt), with_plugin_data=False), {}
            )
        self.assertFalse(self.plugin_data.exists())

    def test_same_session_source_free_marker_replaces_then_named_source_discards(self) -> None:
        self.run_hook(
            self.prompt_payload(
                "Schedule an Excel spreadsheet every week", "turn-excel", "session-shared"
            )
        )
        self.run_hook(
            self.prompt_payload("Review a Word document", "turn-word", "session-shared")
        )

        entries = self.pending_entries()
        self.assertEqual(len(entries), 1)
        self.assertIn("物件：Word", str(entries[0]["expected"]))

        (self.workspace / "budget.xlsx").touch()
        named = self.run_hook(
            self.prompt_payload("Review budget.xlsx", "turn-named", "session-shared")
        )

        self.assertIsNotNone(named)
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

    def test_stale_and_future_markers_expire_before_new_source_free_intake(self) -> None:
        self.run_hook(
            self.prompt_payload("Schedule an Excel spreadsheet", "turn-stale", "session-stale")
        )
        entries = self.pending_entries()
        stale_key = str(entries[0]["key"])
        entries[0]["created_at"] = 0
        (self.plugin_data / "pending_intakes.json").write_text(
            json.dumps({"entries": entries}), encoding="utf-8"
        )

        self.run_hook(
            self.prompt_payload("Review a Word document", "turn-word", "session-word")
        )
        entries = self.pending_entries()
        self.assertNotIn(stale_key, {str(entry["key"]) for entry in entries})
        future_key = str(entries[0]["key"])
        entries[0]["created_at"] = 2**31
        (self.plugin_data / "pending_intakes.json").write_text(
            json.dumps({"entries": entries}), encoding="utf-8"
        )

        self.run_hook(
            self.prompt_payload("Schedule a PDF review", "turn-pdf", "session-pdf")
        )
        entries = self.pending_entries()
        self.assertNotIn(future_key, {str(entry["key"]) for entry in entries})
        self.assertEqual(len(entries), 1)

    def test_wrong_or_missing_event_noops_without_plugin_data_state(self) -> None:
        for payload in (
            {"hook_event_name": "SessionStart"},
            {"prompt": "Schedule an Excel spreadsheet"},
        ):
            self.assertEqual(self.run_hook(payload, with_plugin_data=False), {})

        self.assertFalse(self.plugin_data.exists())

    def test_missing_or_blank_identity_refuses_source_free_and_named_prompts(self) -> None:
        source_free = self.prompt_payload("Schedule an Excel spreadsheet")
        source_free.pop("session_id")
        self.assertIsNone(self.run_hook(source_free, expected_returncode=1))
        self.assertFalse(self.plugin_data.exists())

        (self.workspace / "budget.xlsx").touch()
        named = self.prompt_payload("Review budget.xlsx")
        named["turn_id"] = ""
        self.assertIsNone(self.run_hook(named, expected_returncode=1))
        self.assertFalse(self.plugin_data.exists())

    def test_hardlinked_pending_intake_refuses_without_touching_outside_sentinel(self) -> None:
        self.plugin_data.mkdir()
        sentinel = self.base / "outside-pending.json"
        sentinel.write_text('{"entries":[]}', encoding="utf-8")
        os.link(sentinel, self.plugin_data / "pending_intakes.json")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()

        self.assertIsNone(
            self.run_hook(
                self.prompt_payload("Schedule an Excel spreadsheet"), expected_returncode=1
            )
        )
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_linked_pending_intake_refuses_without_touching_outside_sentinel(self) -> None:
        self.plugin_data.mkdir()
        outside = self.base / "outside-pending"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        self.create_directory_link(self.plugin_data / "pending_intakes.json", outside)

        self.assertIsNone(
            self.run_hook(
                self.prompt_payload("Schedule an Excel spreadsheet"), expected_returncode=1
            )
        )
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_pending_and_workspace_dedup_state_excludes_raw_prompt_and_session(self) -> None:
        session_id = "session-private-value"
        source_free_prompt = "Schedule an Excel spreadsheet with private wording"
        self.run_hook(self.prompt_payload(source_free_prompt, session_id=session_id))

        pending_text = (self.plugin_data / "pending_intakes.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(source_free_prompt, pending_text)
        self.assertNotIn(session_id, pending_text)

        source = self.workspace / "private-budget.xlsx"
        source.touch()
        named_prompt = "Review private-budget.xlsx with private wording"
        self.run_hook(self.prompt_payload(named_prompt, "turn-named", session_id))

        workspace_root = next((self.plugin_data / "workspaces").iterdir())
        dedup_text = (workspace_root / "hook_dedup.json").read_text(encoding="utf-8")
        self.assertNotIn(named_prompt, dedup_text)
        self.assertNotIn(session_id, dedup_text)
        self.assertNotIn(os.fspath(source), dedup_text)
        keys = json.loads(dedup_text)["keys"]
        self.assertEqual(len(keys), 1)
        self.assertRegex(keys[0], r"\A[0-9a-f]{64}\Z")


if __name__ == "__main__":
    unittest.main()

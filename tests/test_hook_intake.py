from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from tests.hook_test_support import HookCliFixture, ROOT


ENTRYPOINT = ROOT / "hooks" / "intake_router_hook.py"


class IntakeRoutingCase(HookCliFixture):
    def invoke(self, payload: dict[str, object], *, data: bool = True) -> dict[str, object] | None:
        completed = self.run_json(ENTRYPOINT, payload, data_root=self.plugin_data if data else None)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def context(self, prompt: str) -> str:
        result = self.invoke(self.prompt(prompt))
        self.assertIsNotNone(result)
        value = result["hookSpecificOutput"]["additionalContext"]
        self.assertIsInstance(value, str)
        return value

    def test_direct_named_source_injects_office_context_once(self) -> None:
        (self.workspace / "budget.xlsx").touch()

        context = self.context("Review budget.xlsx")

        self.assertTrue(context.startswith("<office-os-intake>\n"))
        self.assertEqual(context.count("Office.md"), 1)
        self.assertEqual(context.count("Excel.md"), 1)
        self.assertIsNone(self.invoke(self.prompt("Review budget.xlsx")))

    def test_source_free_envelope_writes_only_pending_marker(self) -> None:
        context = self.context("Schedule an Excel spreadsheet every week")

        self.assertIn("<office-os-source-free-intake>", context)
        self.assertTrue((self.plugin_data / "pending_intakes.json").is_file())
        self.assertFalse((self.plugin_data / "workspaces").exists())
        entry = self.pending_entries()[0]
        canonical = "意圖：排程｜物件：Excel｜權限：唯讀｜檢查：快速\nExcel 來源檔或資料夾路徑是什麼？"
        self.assertEqual(str(entry["expected"]), canonical)
        self.assertIn(canonical, context)
        self.assertEqual(context.count("Excel 來源檔或資料夾路徑是什麼？"), 1)
        self.assertIn("FINAL USER-VISIBLE REPLY MUST BE EXACTLY TWO NON-EMPTY LINES.", context)
        self.assertIn("first user-visible message MUST be one compact classification-and-skill-rationale sentence", context)
        self.assertIn("Copy the two lines inside <required-final-reply> verbatim as the entire final reply", context)
        self.assertIn("The Stop hook validates this exact final reply once", context)
        stored = (self.plugin_data / "pending_intakes.json").read_text(encoding="utf-8")
        self.assertNotIn("Schedule an Excel spreadsheet every week", stored)
        self.assertNotIn("session-1", stored)

    def test_quoted_absolute_nested_source_routes_as_named_intake(self) -> None:
        source = self.workspace / "nested" / "reports" / "budget.xlsx"
        source.parent.mkdir(parents=True)
        source.touch()

        context = self.context(f'Review "{source}"')

        self.assertTrue(context.startswith("<office-os-intake>\n"))
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

    def test_extension_only_and_missing_filename_remain_source_free(self) -> None:
        for prompt in ("Create a new .xlsx file", "Update missing-report.xlsx every week"):
            with self.subTest(prompt=prompt):
                self.context(prompt)
                self.assertEqual(len(self.pending_entries()), 1)

    def test_url_remains_source_free_without_workspace_state(self) -> None:
        context = self.context("Review https://example.invalid/report.xlsx")
        self.assertIn("<office-os-source-free-intake>", context)
        self.assertFalse((self.plugin_data / "workspaces").exists())

    def test_single_object_schedule_and_cross_file_references_route_once(self) -> None:
        (self.workspace / "budget.xlsx").touch()
        (self.workspace / "summary.docx").touch()

        context = self.context("Schedule budget.xlsx and summary.docx every week")

        self.assertIn("Office.md", context)
        self.assertNotIn("<office-os-source-free-intake>", context)

    def test_correction_prompt_never_opens_an_intake(self) -> None:
        text = "The source-free Office intake final reply was not canonical. Return exactly these two lines and nothing else."

        self.assertIsNone(self.invoke(self.prompt(text)))
        self.assertFalse(self.plugin_data.exists())

    def test_wrong_or_unrelated_event_noops_before_plugin_data(self) -> None:
        for payload in ({"hook_event_name": "SessionStart"}, {"prompt": "Schedule an Excel spreadsheet"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.invoke(payload, data=False), {})
        self.assertFalse(self.plugin_data.exists())

    def test_missing_or_blank_identity_refuses_without_state(self) -> None:
        missing = self.prompt("Schedule an Excel spreadsheet")
        missing.pop("session_id")
        blank = self.prompt("Schedule an Excel spreadsheet")
        blank["turn_id"] = ""
        for payload in (missing, blank):
            with self.subTest(payload=payload):
                completed = self.run_json(ENTRYPOINT, payload, data_root=self.plugin_data)
                self.assertEqual((completed.returncode, completed.stdout), (1, ""))
                self.assertFalse(self.plugin_data.exists())


if __name__ == "__main__":
    unittest.main()

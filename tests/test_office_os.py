from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skills" / "office-os" / "scripts" / "office_os.py"


def write_xlsx(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>
            """,
        )
        package.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook
              xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Summary" sheetId="1" r:id="rId1"/></sheets>
            </workbook>
            """,
        )
        package.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet1.xml"/>
            </Relationships>
            """,
        )
        package.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{text}</t></is></c></row></sheetData>
            </worksheet>
            """,
        )


def write_docx(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        package.writestr(
            "word/document.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>季度結論</w:t></w:r></w:p>
                <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """,
        )


def write_pptx(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        package.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        package.writestr(
            "ppt/slides/slide1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
        )


def xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as package:
        return package.read("xl/worksheets/sheet1.xml").decode("utf-8")


class CoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def run_core(
        self, command: str, *arguments: str, expected: int = 0
    ) -> tuple[dict, subprocess.CompletedProcess[str]]:
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        completed = subprocess.run(
            [
                sys.executable,
                os.fspath(CORE),
                command,
                *arguments,
                "--cwd",
                os.fspath(self.workspace),
            ],
            cwd=self.workspace,
            env=environment,
            input="",
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return json.loads(completed.stdout), completed

    def workspace_data(self) -> Path:
        roots = list((self.plugin_data / "workspaces").iterdir())
        self.assertEqual(len(roots), 1)
        return roots[0]

    def test_index_upserts_queries_chinese_and_purges_deleted_sources(self) -> None:
        workbook = self.workspace / "budget.xlsx"
        document = self.workspace / "plan.docx"
        presentation = self.workspace / "briefing.pptx"
        write_xlsx(workbook, "台北辦公室季度報告")
        write_docx(document, "本季優先處理採購資料")
        write_pptx(presentation, "執行摘要與下一步")

        first, _ = self.run_core("index", "--path", os.fspath(self.workspace))
        self.assertEqual(first["discovered"], 3)
        self.assertEqual(first["indexed"], 3)
        self.assertEqual(first["fts_tokenizer"], "trigram")

        query, _ = self.run_core("query", "--text", "辦公室季度")
        self.assertEqual(query["count"], 1)
        self.assertEqual(query["results"][0]["object"], "excel")
        self.assertIn("sheet=Summary", query["results"][0]["locator"])

        second, _ = self.run_core("index", "--path", os.fspath(self.workspace))
        self.assertEqual(second["unchanged"], 3)
        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            self.assertEqual(
                database.execute("SELECT count(*) FROM documents").fetchone()[0], 3
            )
        finally:
            database.close()

        document.unlink()
        third, _ = self.run_core("index", "--path", os.fspath(self.workspace))
        self.assertEqual(third["purged"], 1)
        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            self.assertEqual(
                database.execute("SELECT count(*) FROM documents").fetchone()[0], 2
            )
        finally:
            database.close()

    def test_sensitive_and_macro_files_are_metadata_only(self) -> None:
        confidential = self.workspace / "機密薪資.xlsx"
        write_xlsx(confidential, "不得進入全文索引")
        macro = self.workspace / "automation.xlsm"
        macro.write_bytes(b"macro placeholder")

        result, _ = self.run_core("index", "--path", os.fspath(self.workspace))
        self.assertEqual(result["metadata_only"], 2)
        query, _ = self.run_core("query", "--text", "不得進入")
        self.assertEqual(query["count"], 0)

        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            policies = database.execute(
                "SELECT extension, index_policy, sensitivity FROM documents ORDER BY extension"
            ).fetchall()
        finally:
            database.close()
        self.assertEqual(
            policies,
            [
                (".xlsm", "metadata-only", "metadata-only"),
                (".xlsx", "metadata-only", "metadata-only"),
            ],
        )

    def test_single_file_index_does_not_purge_siblings(self) -> None:
        one = self.workspace / "one.xlsx"
        two = self.workspace / "two.xlsx"
        write_xlsx(one, "第一份")
        write_xlsx(two, "第二份")
        self.run_core("index", "--path", os.fspath(self.workspace))
        write_xlsx(one, "第一份更新")
        result, _ = self.run_core("index", "--path", os.fspath(one))
        self.assertEqual(result["purged"], 0)
        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            count = database.execute("SELECT count(*) FROM documents").fetchone()[0]
        finally:
            database.close()
        self.assertEqual(count, 2)

    def test_scheduled_publish_keeps_three_backups_and_unchanged_is_noop(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source-0")
        write_xlsx(candidate, "output-0")

        first, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "季度整理",
            "--mode",
            "scheduled",
        )
        target = Path(first["target"])
        self.assertTrue(target.is_file())
        self.assertIn("output-0", xlsx_text(target))

        for number in range(1, 5):
            write_xlsx(source, f"source-{number}")
            write_xlsx(candidate, f"output-{number}")
            self.run_core(
                "publish",
                "--candidate",
                os.fspath(candidate),
                "--source",
                os.fspath(source),
                "--task",
                "季度整理",
                "--mode",
                "scheduled",
            )

        self.assertIn("output-4", xlsx_text(target))
        self.assertIn("output-3", xlsx_text(Path(f"{target}.bak.1")))
        self.assertIn("output-2", xlsx_text(Path(f"{target}.bak.2")))
        self.assertIn("output-1", xlsx_text(Path(f"{target}.bak.3")))
        self.assertFalse(Path(f"{target}.bak.4").exists())

        before = {
            path.name: path.read_bytes()
            for path in target.parent.iterdir()
            if path == target or ".bak." in path.name
        }
        write_xlsx(candidate, "should-not-publish")
        unchanged, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "季度整理",
            "--mode",
            "scheduled",
        )
        self.assertEqual(unchanged["status"], "unchanged")
        after = {
            path.name: path.read_bytes()
            for path in target.parent.iterdir()
            if path == target or ".bak." in path.name
        }
        self.assertEqual(before, after)

    def test_manual_publish_has_no_history_and_invalid_candidate_preserves_output(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source")
        write_xlsx(candidate, "good-output")
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "人工更新",
        )
        target = Path(result["target"])
        expected = target.read_bytes()
        self.assertEqual(list(target.parent.glob("*.bak.*")), [])

        bad = self.workspace / "bad.xlsx"
        bad.write_text("not an Open XML package", encoding="utf-8")
        error, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(bad),
            "--source",
            os.fspath(source),
            "--task",
            "人工更新",
            expected=2,
        )
        self.assertEqual(error["status"], "error")
        self.assertEqual(target.read_bytes(), expected)

    def test_cross_file_noop_digest_covers_every_source(self) -> None:
        workbook = self.workspace / "source.xlsx"
        document = self.workspace / "source.docx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(workbook, "workbook-v1")
        write_docx(document, "document-v1")
        write_xlsx(candidate, "combined-v1")
        first, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(workbook),
            "--source",
            os.fspath(document),
            "--task",
            "跨檔案整合",
            "--mode",
            "scheduled",
        )
        target = Path(first["target"])

        unchanged, _ = self.run_core(
            "needs-run",
            "--source",
            os.fspath(workbook),
            "--source",
            os.fspath(document),
            "--task",
            "跨檔案整合",
            "--extension",
            ".xlsx",
        )
        self.assertFalse(unchanged["needs_run"])

        write_docx(document, "document-v2")
        changed, _ = self.run_core(
            "needs-run",
            "--source",
            os.fspath(workbook),
            "--source",
            os.fspath(document),
            "--task",
            "跨檔案整合",
            "--extension",
            ".xlsx",
        )
        self.assertTrue(changed["needs_run"])
        self.assertTrue(target.exists())

    def test_publish_rejects_target_outside_fixed_output_directory(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        outside = self.workspace / "outside.xlsx"
        write_xlsx(source, "source")
        write_xlsx(candidate, "candidate")
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "固定位置",
            "--target",
            os.fspath(outside),
            expected=2,
        )
        self.assertEqual(result["status"], "error")
        self.assertFalse(outside.exists())

    def test_task_start_fingerprint_blocks_publish_after_source_mutation(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source-v1")
        write_xlsx(candidate, "output-v1")
        first, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "來源保護",
        )
        target = Path(first["target"])
        previous = target.read_bytes()

        self.run_core(
            "begin",
            "--task",
            "來源保護",
            "--source",
            os.fspath(source),
            "--intent",
            "update",
            "--object",
            "excel",
            "--permission",
            "fixed-output-write",
            "--qa",
            "fast",
            "--units",
            "1",
        )
        write_xlsx(source, "source-was-mutated")
        write_xlsx(candidate, "output-v2")
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "來源保護",
            expected=2,
        )
        self.assertIn("task-start fingerprint", result["error"])
        self.assertEqual(target.read_bytes(), previous)
        self.run_core("fail", "--reason", "test cleanup")

    def test_scheduled_single_flight_and_latest_summary_are_bounded(self) -> None:
        arguments = (
            "--task",
            "每日報表",
            "--intent",
            "update",
            "--object",
            "excel",
            "--permission",
            "scheduled-overwrite",
            "--qa",
            "fast",
            "--units",
            "2",
            "--mode",
            "scheduled",
        )
        first, _ = self.run_core("begin", *arguments)
        self.assertEqual(first["status"], "executing")
        second, _ = self.run_core("begin", *arguments)
        self.assertEqual(second["status"], "overlap_skipped")
        manual_arguments = list(arguments)
        manual_arguments[-1] = "manual"
        manual, _ = self.run_core("begin", *manual_arguments)
        self.assertEqual(manual["status"], "overlap_skipped")
        active = json.loads(
            (self.workspace_data() / "run_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(active["run_id"], first["run_id"])
        self.run_core("complete", "--summary", "第一輪完成")

        third, _ = self.run_core("begin", *arguments)
        self.assertEqual(third["status"], "executing")
        self.run_core("complete", "--summary", "第二輪完成")
        data = self.workspace_data()
        self.assertFalse((data / "run_state.json").exists())
        self.assertFalse((data / "single-flight.lock").exists())
        latest = json.loads((data / "latest_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["summary"], "第二輪完成")


if __name__ == "__main__":
    unittest.main()

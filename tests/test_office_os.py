# noqa: SIZE_OK - Task 13 keeps one consolidated behavioral suite for the preserved Office core.
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from collections.abc import Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skills" / "office-os" / "scripts" / "office_os.py"
CANDIDATES = ROOT / "skills" / "office-os" / "scripts" / "office_candidates.py"


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

    def begin_publish_run(  # noqa: DICT_OK - heterogeneous CLI JSON fixture state.
        self,
        task: str,
        sources: Sequence[Path],
        mode: str = "manual",
    ) -> dict:
        arguments = [
            "--task",
            task,
            "--intent",
            "update",
            "--object",
            "office",
            "--permission",
            "scheduled-overwrite" if mode == "scheduled" else "fixed-output-write",
            "--qa",
            "fast",
            "--units",
            "1",
            "--mode",
            mode,
        ]
        for source in sources:
            arguments.extend(("--source", os.fspath(source)))
        state, _ = self.run_core("begin", *arguments)
        self.assertEqual(state["status"], "executing")
        return state

    def test_index_upserts_queries_chinese_and_purges_deleted_sources(self) -> None:
        workbook = self.workspace / "budget.xlsx"
        document = self.workspace / "plan.docx"
        presentation = self.workspace / "briefing.pptx"
        write_xlsx(workbook, "台北辦公室季度報告")
        write_docx(document, "本季優先處理採購資料")
        write_pptx(presentation, "執行摘要與下一步")

        first, _ = self.run_core(
            "index",
            "--path",
            os.fspath(self.workspace),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )
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

    def test_index_defaults_to_metadata_until_full_text_root_is_granted(self) -> None:
        workbook = self.workspace / "consent.xlsx"
        write_xlsx(workbook, "需要明確同意才能保存")

        first, _ = self.run_core("index", "--path", os.fspath(self.workspace))
        self.assertEqual(first["metadata_only"], 1)
        before_consent, _ = self.run_core("query", "--text", "明確同意")
        self.assertEqual(before_consent["count"], 0)

        granted, _ = self.run_core(
            "index",
            "--path",
            os.fspath(self.workspace),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )
        self.assertEqual(granted["indexed"], 1)
        after_consent, _ = self.run_core("query", "--text", "明確同意")
        self.assertEqual(after_consent["count"], 1)

        repeated, _ = self.run_core("index", "--path", os.fspath(self.workspace))
        self.assertEqual(repeated["unchanged"], 1)

    def test_index_rejects_paths_outside_the_configured_workspace_root(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        write_xlsx(outside / "private.xlsx", "不屬於目前工作區")

        result, _ = self.run_core(
            "index",
            "--path",
            os.fspath(outside),
            expected=2,
        )
        self.assertIn("outside the configured workspace root", result["error"])

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

    def test_query_omits_chunks_when_source_changed_since_index(self) -> None:
        workbook = self.workspace / "stale.xlsx"
        write_xlsx(workbook, "原始金額120萬")
        self.run_core(
            "index",
            "--path",
            os.fspath(self.workspace),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )

        write_xlsx(workbook, "新金額999萬")
        stale, _ = self.run_core("query", "--text", "原始金額")
        self.assertEqual(stale["count"], 0)

    def test_read_only_run_cannot_publish(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source")
        write_xlsx(candidate, "candidate")
        self.run_core(
            "begin",
            "--task",
            "唯讀檢查",
            "--source",
            os.fspath(source),
            "--intent",
            "inspect",
            "--object",
            "excel",
            "--permission",
            "read-only",
            "--qa",
            "fast",
            "--units",
            "1",
        )

        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "唯讀檢查",
            expected=2,
        )
        self.assertIn("does not authorize publishing", result["error"])
        self.assertFalse((self.workspace / "Office OS Output").exists())

    def test_minimal_open_xml_package_cannot_replace_previous_output(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source")
        write_xlsx(candidate, "valid-output")
        self.begin_publish_run("套件驗證", (source,))
        first, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "套件驗證",
        )
        target = Path(first["target"])
        previous = target.read_bytes()

        minimal = self.workspace / "minimal.xlsx"
        with zipfile.ZipFile(minimal, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("xl/workbook.xml", "<workbook/>")
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(minimal),
            "--source",
            os.fspath(source),
            "--task",
            "套件驗證",
            expected=2,
        )
        self.assertIn("[Content_Types].xml", result["error"])
        self.assertEqual(target.read_bytes(), previous)

    def test_scheduled_publish_requires_its_active_single_flight_lease(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source")
        write_xlsx(candidate, "candidate")
        self.begin_publish_run("排程鎖", (source,), mode="scheduled")
        (self.workspace_data() / "single-flight.lock").unlink()

        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "排程鎖",
            "--mode",
            "scheduled",
            expected=2,
        )
        self.assertIn("single-flight lease", result["error"])
        self.assertFalse((self.workspace / "Office OS Output").exists())

    def test_pdf_is_metadata_readable_but_never_a_publish_candidate(self) -> None:
        pdf = self.workspace / "review.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        indexed, _ = self.run_core("index", "--path", os.fspath(pdf))
        self.assertEqual(indexed["metadata_only"], 1)

        self.begin_publish_run("PDF 唯讀", (pdf,))
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(pdf),
            "--source",
            os.fspath(pdf),
            "--task",
            "PDF 唯讀",
            expected=2,
        )
        self.assertIn("Writable candidates must be", result["error"])

    def test_scheduled_publish_keeps_three_backups_and_unchanged_is_noop(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(source, "source-0")
        write_xlsx(candidate, "output-0")

        self.begin_publish_run("季度整理", (source,), mode="scheduled")
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
        self.run_core("complete", "--summary", "published")

        for number in range(1, 5):
            write_xlsx(source, f"source-{number}")
            write_xlsx(candidate, f"output-{number}")
            self.begin_publish_run("季度整理", (source,), mode="scheduled")
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
            self.run_core("complete", "--summary", f"published-{number}")

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
        self.begin_publish_run("季度整理", (source,), mode="scheduled")
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
        self.run_core("complete", "--summary", "unchanged")
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
        self.begin_publish_run("人工更新", (source,))
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

    def test_managed_candidate_lifecycle_is_bounded_across_success_failure_and_restart(self) -> None:
        source = self.workspace / "source.xlsx"
        candidate_root = self.plugin_data / "officecli-candidates"
        candidate = candidate_root / "run-1" / "candidate.xlsx"
        write_xlsx(source, "source")
        write_xlsx(candidate, "published-output")
        source_before = source.read_bytes()

        self.begin_publish_run("受管候選成功", (source,))
        published, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "受管候選成功",
        )
        self.assertEqual(published["status"], "published")
        self.assertTrue(published["candidate_removed"])
        self.assertFalse(candidate.exists())
        self.assertEqual(source.read_bytes(), source_before)
        self.run_core("complete", "--summary", "published")

        failed = candidate_root / "run-2" / "invalid.xlsx"
        failed.parent.mkdir(parents=True)
        failed.write_text("invalid", encoding="utf-8")
        self.begin_publish_run("受管候選失敗", (source,))
        error, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(failed),
            "--source",
            os.fspath(source),
            "--task",
            "受管候選失敗",
            expected=2,
        )
        self.assertEqual(error["status"], "error")
        self.assertTrue(failed.exists(), "a failed candidate stays available for revision")
        failed_state, _ = self.run_core("fail", "--reason", "owner stopped revision")
        self.assertTrue(failed_state["candidate_removed"])
        self.assertFalse(failed.exists())

        candidate_root.mkdir(parents=True, exist_ok=True)
        for number in range(40):
            (candidate_root / f"interrupted-{number:02d}.xlsx").write_bytes(b"candidate")
        self.begin_publish_run("受管候選回收", (source,))
        remaining = [path for path in candidate_root.rglob("*") if path.is_file()]
        self.assertLessEqual(len(remaining), 32)
        cleanup, _ = self.run_core("cleanup", "--older-than-seconds", "0")
        self.assertEqual(cleanup["managed_candidates"]["remaining_files"], 0)
        self.assertEqual(
            [path for path in candidate_root.rglob("*") if path.is_file()], []
        )

    def test_candidate_cleanup_refuses_a_linked_staging_root(self) -> None:
        candidate_root = self.plugin_data / "officecli-candidates"
        candidate_root.parent.mkdir(parents=True)
        outside = self.base / "outside-candidates"
        outside.mkdir()
        sentinel = outside / "sentinel.xlsx"
        sentinel.write_bytes(b"outside")
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(candidate_root), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            candidate_root.symlink_to(outside, target_is_directory=True)
        try:
            error, _ = self.run_core(
                "begin",
                "--task",
                "拒絕連結候選根",
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
                expected=2,
            )
            self.assertIn("linked", error["error"])
            self.assertEqual(sentinel.read_bytes(), b"outside")
        finally:
            if os.path.lexists(candidate_root):
                if os.name == "nt":
                    os.rmdir(candidate_root)
                else:
                    candidate_root.unlink()

    def test_candidate_cleanup_skips_linked_entries_without_touching_outside_files(self) -> None:
        candidate_root = self.plugin_data / "officecli-candidates"
        candidate_root.mkdir(parents=True)
        outside = self.base / "outside-entry"
        outside.mkdir()
        sentinel = outside / "sentinel.xlsx"
        sentinel.write_bytes(b"outside")
        linked = candidate_root / "linked"
        if os.name == "nt":
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
        else:
            linked.symlink_to(outside, target_is_directory=True)
        try:
            cleanup, _ = self.run_core("cleanup", "--older-than-seconds", "0")
            self.assertEqual(sentinel.read_bytes(), b"outside")
            self.assertIn(
                os.fspath(linked), cleanup["managed_candidates"]["skipped_links"]
            )
            self.assertTrue(os.path.lexists(linked))
        finally:
            if os.path.lexists(linked):
                if os.name == "nt":
                    os.rmdir(linked)
                else:
                    linked.unlink()

    def test_candidate_cleanup_enforces_the_byte_quota(self) -> None:
        spec = importlib.util.spec_from_file_location("office_candidates_test", CANDIDATES)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        root = self.plugin_data / "officecli-candidates"
        root.mkdir(parents=True)
        for number in range(3):
            (root / f"candidate-{number}.xlsx").write_bytes(b"x" * 10)
        with mock.patch.object(module, "MAX_CANDIDATE_FILES", 10):
            with mock.patch.object(module, "MAX_CANDIDATE_BYTES", 20):
                result = module.prune_managed_candidates(
                    self.plugin_data, older_than_seconds=86_400
                )
        self.assertEqual(result["remaining_files"], 2)
        self.assertEqual(result["remaining_bytes"], 20)
        self.assertEqual(result["removed_count"], 1)

    def test_cross_file_noop_digest_covers_every_source(self) -> None:
        workbook = self.workspace / "source.xlsx"
        document = self.workspace / "source.docx"
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(workbook, "workbook-v1")
        write_docx(document, "document-v1")
        write_xlsx(candidate, "combined-v1")
        self.begin_publish_run(
            "跨檔案整合", (workbook, document), mode="scheduled"
        )
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
        self.run_core("complete", "--summary", "published")

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
        self.begin_publish_run("固定位置", (source,))
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
        self.begin_publish_run("來源保護", (source,))
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
        self.run_core("complete", "--summary", "baseline")

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

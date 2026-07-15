# noqa: SIZE_OK - Task 13 keeps one consolidated behavioral suite for the preserved Office core.
from __future__ import annotations

import ctypes
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
import zipfile
from collections.abc import Sequence
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skills" / "office-os" / "scripts" / "office_os.py"
CANDIDATES = ROOT / "skills" / "office-os" / "scripts" / "office_candidates.py"
AUTHORITY = ROOT / "scripts" / "officecli-mcp" / "authority.cjs"


class OfficeOSTestSetupError(RuntimeError):
    pass


def load_core_module() -> ModuleType:
    module_name = "office_os_behavior_test"
    spec = importlib.util.spec_from_file_location(module_name, CORE)
    if spec is None or spec.loader is None:
        raise OfficeOSTestSetupError("Unable to load Office OS core for behavioral test.")
    module = importlib.util.module_from_spec(spec)
    scripts = os.fspath(CORE.parent)
    sys.path.insert(0, scripts)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
        sys.modules.pop(module_name, None)
    return module


def write_xlsx(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        package.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
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
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
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
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/ppt/presentation.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            "</Types>",
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/>'
            "</Relationships>",
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
        package.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            'Target="slides/slide1.xml"/>'
            "</Relationships>",
        )


def xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as package:
        return package.read("xl/worksheets/sheet1.xml").decode("utf-8")


def short_windows_path(path: Path) -> Path:
    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetShortPathNameW(
        os.fspath(path), buffer, len(buffer)
    )
    if not length:
        raise OfficeOSTestSetupError("Windows did not provide a short path for fixture.")
    return Path(buffer.value)


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
        completed = self.run_core_raw(command, *arguments)
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return json.loads(completed.stdout), completed

    def run_core_raw(
        self, command: str, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        return subprocess.run(
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
        if mode == "scheduled":
            self.assertEqual(state["status"], "executing")
        else:
            self.assertEqual(state["status"], "awaiting_confirmation")
            state, _ = self.run_core("confirm")
            self.assertEqual(state["status"], "executing")
        return state

    def candidate_for(self, state: dict, name: str = "candidate.xlsx") -> Path:
        return Path(state["candidate_directory"]) / name

    def test_core_requires_hook_injected_plugin_data(self) -> None:
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment.pop("CLAUDE_PLUGIN_DATA", None)
        completed = subprocess.run(
            [sys.executable, os.fspath(CORE), "status", "--cwd", os.fspath(self.workspace)],
            cwd=self.workspace,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("PLUGIN_DATA", json.loads(completed.stdout)["error"])

    def test_core_rejects_claude_only_plugin_data(self) -> None:
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment["CLAUDE_PLUGIN_DATA"] = os.fspath(self.plugin_data)
        completed = subprocess.run(
            [sys.executable, os.fspath(CORE), "status", "--cwd", os.fspath(self.workspace)],
            cwd=self.workspace,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("PLUGIN_DATA", json.loads(completed.stdout)["error"])
        self.assertFalse(self.plugin_data.exists())

    def test_begin_requires_source_before_creating_workspace_or_candidate(self) -> None:
        completed = self.run_core_raw(
            "begin",
            "--task",
            "source required",
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

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--source", completed.stderr)
        self.assertFalse(self.plugin_data.exists())

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

    def test_query_path_prefix_is_boundary_safe_and_treats_like_tokens_literally(self) -> None:
        scoped = self.workspace / "client_100%"
        sibling = self.workspace / "clientX100Z"
        write_xlsx(scoped / "owned.xlsx", "path prefix exact marker")
        write_xlsx(sibling / "unrelated.xlsx", "path prefix exact marker")
        self.run_core(
            "index",
            "--path",
            os.fspath(self.workspace),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )

        result, _ = self.run_core(
            "query",
            "--text",
            "path prefix exact marker",
            "--path-prefix",
            os.fspath(scoped),
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(Path(result["results"][0]["path"]).name, "owned.xlsx")

    def test_pdf_remains_metadata_only_after_full_text_root_is_granted(self) -> None:
        pdf = self.workspace / "consented.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

        indexed, _ = self.run_core(
            "index",
            "--path",
            os.fspath(pdf),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )
        self.assertEqual(indexed["metadata_only"], 1)
        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            row = database.execute(
                """
                SELECT d.index_policy, d.index_status, COUNT(c.id)
                FROM documents AS d
                LEFT JOIN chunks AS c ON c.document_id = d.id
                WHERE d.extension = '.pdf'
                GROUP BY d.id
                """,
            ).fetchone()
        finally:
            database.close()
        self.assertEqual(row, ("metadata-only", "complete", 0))

    def test_index_archive_limits_keep_oversized_open_xml_metadata_only(self) -> None:
        module = load_core_module()
        document = self.workspace / "limited.docx"
        write_docx(document, "archive limit marker")

        with mock.patch.object(module, "MAX_INDEX_PACKAGE_MEMBERS", 1, create=True):
            sensitivity, reason = module.detect_sensitivity(document)
            self.assertEqual(sensitivity, "metadata-only")
            self.assertIn("index limit", reason)
            with self.assertRaises(module.OfficeOSError):
                module.extract_docx(document)

    def test_index_over_limit_document_clears_chunks_and_retries(self) -> None:
        module = load_core_module()
        document = self.workspace / "over-limit.docx"
        write_docx(
            document,
            "".join(f"{number:08x}" for number in range(1_600)),
        )
        arguments = SimpleNamespace(
            cwd=os.fspath(self.workspace),
            path=[os.fspath(document)],
            allow_sensitive_content=[],
            grant_full_text_root=[os.fspath(self.workspace)],
            revoke_full_text_root=[],
            metadata_only=False,
        )
        messages: list[dict] = []

        with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(self.plugin_data)}):
            with mock.patch.object(module, "MAX_KNOWLEDGE_CHUNKS", 1):
                with mock.patch.object(module, "json_print", side_effect=messages.append):
                    self.assertEqual(module.command_index(arguments), 0)
                    self.assertEqual(module.command_index(arguments), 0)

        self.assertEqual(messages[0]["errors"], 1)
        self.assertEqual(messages[1]["errors"], 1)
        self.assertEqual(messages[1]["unchanged"], 0)
        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            row = database.execute(
                """
                SELECT d.index_status, d.error, COUNT(c.id)
                FROM documents AS d
                LEFT JOIN chunks AS c ON c.document_id = d.id
                WHERE d.path = ?
                GROUP BY d.id
                """,
                (os.path.normcase(os.fspath(module.canonical_path(document))),),
            ).fetchone()
        finally:
            database.close()
        self.assertEqual(row[0], "error")
        self.assertIn("limit", row[1])
        self.assertEqual(row[2], 0)

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_canonical_path_expands_windows_short_name(self) -> None:
        module = load_core_module()
        document = self.workspace / "short-name.docx"
        write_docx(document, "short-name")
        short = short_windows_path(document)
        if os.path.normcase(os.fspath(short)) == os.path.normcase(os.fspath(document)):
            self.skipTest("fixture path has no distinct Windows short alias")

        with mock.patch.object(module.os.path, "realpath", return_value=os.fspath(short)):
            canonical = module.canonical_path(document)
        self.assertTrue(os.path.samefile(canonical, document))
        self.assertNotEqual(
            os.path.normcase(os.fspath(canonical)), os.path.normcase(os.fspath(short))
        )

    def test_knowledge_map_retention_caps_documents_chunks_and_text(self) -> None:
        module = load_core_module()
        directory = self.base / "knowledge-map-retention"
        directory.mkdir()
        now_ns = time.time_ns()
        sources = (
            ("oldest.xlsx", 0, ("old!",)),
            ("middle.xlsx", 1, ("mid",)),
            ("latest.xlsx", 2, ("four", "five")),
        )
        paths: list[tuple[Path, tuple[str, ...]]] = []
        for name, offset, texts in sources:
            path = self.workspace / name
            path.write_bytes(name.encode("utf-8"))
            os.utime(path, ns=(now_ns + offset, now_ns + offset))
            paths.append((path, texts))

        database = module.connect_database(directory)
        try:
            for path, texts in paths:
                module.replace_document(
                    database,
                    path,
                    module.fingerprint(path),
                    "normal",
                    "",
                    "full-text",
                    "complete",
                    [
                        module.chunk(index, f"cell=A{index + 1}", "Summary", text)
                        for index, text in enumerate(texts)
                    ],
                )
        finally:
            module.close_database(database, directory)

        with mock.patch.object(module, "MAX_KNOWLEDGE_DOCUMENTS", 2, create=True):
            with mock.patch.object(module, "MAX_KNOWLEDGE_CHUNKS", 3, create=True):
                with mock.patch.object(module, "MAX_KNOWLEDGE_TEXT_BYTES", 12, create=True):
                    database = module.connect_database(directory)
                    try:
                        documents = database.execute(
                            "SELECT path FROM documents ORDER BY mtime_ns DESC, path ASC"
                        ).fetchall()
                        chunk_count = database.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                        text_chars = database.execute(
                            "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM chunks"
                        ).fetchone()[0]
                    finally:
                        module.close_database(database, directory)

        self.assertEqual(
            [Path(row[0]).name for row in documents],
            ["latest.xlsx", "middle.xlsx"],
        )
        self.assertEqual(chunk_count, 3)
        self.assertLessEqual(text_chars, 12)

        with mock.patch.object(module, "MAX_KNOWLEDGE_DOCUMENTS", 2, create=True):
            with mock.patch.object(module, "MAX_KNOWLEDGE_CHUNKS", 3, create=True):
                with mock.patch.object(module, "MAX_KNOWLEDGE_TEXT_BYTES", 8, create=True):
                    database = module.connect_database(directory)
                    try:
                        documents = database.execute(
                            "SELECT path FROM documents ORDER BY mtime_ns DESC, path ASC"
                        ).fetchall()
                        chunk_count = database.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                        text_bytes = database.execute(
                            "SELECT COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM chunks"
                        ).fetchone()[0]
                    finally:
                        module.close_database(database, directory)

        self.assertEqual([Path(row[0]).name for row in documents], ["latest.xlsx"])
        self.assertEqual(chunk_count, 2)
        self.assertEqual(text_bytes, 8)

    def test_index_isolates_malformed_supported_documents(self) -> None:
        malformed = {
            ".docx": self.workspace / "broken.docx",
            ".xlsx": self.workspace / "broken.xlsx",
            ".pptx": self.workspace / "broken.pptx",
        }
        for extension, path in malformed.items():
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
                package.writestr("[Content_Types].xml", "<Types/>")
                if extension == ".pptx":
                    package.writestr("ppt/slides/slide1.xml", "<not-closed>")
        broken_pdf = self.workspace / "broken.pdf"
        broken_pdf.write_bytes(b"%PDF-1.4\nnot-a-valid-pdf\n%%EOF\n")
        valid = self.workspace / "valid.xlsx"
        write_xlsx(valid, "continues after malformed files")

        result, _ = self.run_core(
            "index",
            "--path",
            os.fspath(self.workspace),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )

        self.assertEqual(
            (result["discovered"], result["indexed"], result["errors"], result["metadata_only"]),
            (5, 5, 3, 1),
        )
        database = sqlite3.connect(self.workspace_data() / "office.db")
        try:
            rows = database.execute(
                "SELECT path, index_status, error FROM documents ORDER BY path"
            ).fetchall()
        finally:
            database.close()
        statuses = {Path(path).name: (status, error) for path, status, error in rows}
        for path in malformed.values():
            status, error = statuses[path.name]
            self.assertEqual(status, "error")
            self.assertTrue(error)
            self.assertLessEqual(len(error), 500)
        self.assertEqual(statuses[broken_pdf.name], ("complete", ""))
        self.assertEqual(statuses[valid.name][0], "complete")

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
        write_xlsx(source, "source")
        state = self.begin_publish_run("套件驗證", (source,))
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "valid-output")
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
        self.run_core("complete", "--summary", "valid package")

        state = self.begin_publish_run("套件驗證", (source,))
        minimal = self.candidate_for(state, "minimal.xlsx")
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
        write_xlsx(source, "source")
        state = self.begin_publish_run("排程鎖", (source,), mode="scheduled")
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "candidate")
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

        state = self.begin_publish_run("PDF 唯讀", (pdf,))
        candidate = self.candidate_for(state, "review.pdf")
        candidate.write_bytes(pdf.read_bytes())
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(pdf),
            "--task",
            "PDF 唯讀",
            expected=2,
        )
        self.assertIn("Writable candidates must be", result["error"])

    def test_scheduled_publish_keeps_three_backups_and_unchanged_is_noop(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source-0")
        state = self.begin_publish_run("季度整理", (source,), mode="scheduled")
        candidate = self.candidate_for(state)
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
        self.run_core("complete", "--summary", "published")

        for number in range(1, 5):
            write_xlsx(source, f"source-{number}")
            state = self.begin_publish_run("季度整理", (source,), mode="scheduled")
            candidate = self.candidate_for(state)
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
        state = self.begin_publish_run("季度整理", (source,), mode="scheduled")
        candidate = self.candidate_for(state)
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
        self.run_core("complete", "--summary", "unchanged")
        after = {
            path.name: path.read_bytes()
            for path in target.parent.iterdir()
            if path == target or ".bak." in path.name
        }
        self.assertEqual(before, after)

    def test_scheduled_publish_rejects_hard_linked_backup_without_replacing_output(
        self,
    ) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source-0")
        state = self.begin_publish_run("backup safety", (source,), mode="scheduled")
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "output-0")
        first, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "backup safety",
            "--mode",
            "scheduled",
        )
        target = Path(first["target"])
        previous = target.read_bytes()
        self.run_core("complete", "--summary", "baseline")

        sentinel = self.base / "outside-backup.xlsx"
        sentinel.write_bytes(b"outside backup sentinel")
        backup = Path(f"{target}.bak.1")
        os.link(sentinel, backup)

        write_xlsx(source, "source-1")
        state = self.begin_publish_run("backup safety", (source,), mode="scheduled")
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "output-1")
        rejected, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "backup safety",
            "--mode",
            "scheduled",
            expected=2,
        )

        self.assertIn("backup", rejected["error"].lower())
        self.assertEqual(target.read_bytes(), previous)
        self.assertTrue(backup.is_file())
        self.assertGreater(backup.stat().st_nlink, 1)
        self.assertEqual(sentinel.read_bytes(), b"outside backup sentinel")
        self.run_core("fail", "--reason", "unsafe backup leaf")

    def test_manual_publish_has_no_history_and_invalid_candidate_preserves_output(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("人工更新", (source,))
        candidate = self.candidate_for(state)
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
        self.run_core("complete", "--summary", "valid manual package")

        state = self.begin_publish_run("人工更新", (source,))
        bad = self.candidate_for(state, "bad.xlsx")
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
        write_xlsx(source, "source")
        state = self.begin_publish_run("受管候選成功", (source,))
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "published-output")
        source_before = source.read_bytes()

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

        state = self.begin_publish_run("受管候選失敗", (source,))
        failed = self.candidate_for(state, "invalid.xlsx")
        failed.write_text("invalid", encoding="utf-8")
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
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
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
                "--source",
                os.fspath(source),
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

    def test_candidate_cleanup_removes_linked_entries_without_touching_outside_files(self) -> None:
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
            self.assertEqual(cleanup["managed_candidates"]["skipped_links"], [])
            self.assertFalse(os.path.lexists(linked))
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

    def test_candidate_inventory_rejects_special_leaves(self) -> None:
        spec = importlib.util.spec_from_file_location("office_candidates_test", CANDIDATES)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        root = self.plugin_data / "officecli-candidates"
        root.mkdir(parents=True)
        special = root / "special-leaf"
        special.touch()
        original_lstat = Path.lstat

        def special_lstat(path: Path):
            if path == special:
                return SimpleNamespace(st_mode=module.stat.S_IFIFO, st_nlink=1)
            return original_lstat(path)

        with mock.patch.object(module, "is_linklike", return_value=False):
            with mock.patch.object(module.Path, "lstat", new=special_lstat):
                with self.assertRaisesRegex(module.CandidateLifecycleError, "ordinary"):
                    module.inventory(root)

    def test_cross_file_noop_digest_covers_every_source(self) -> None:
        workbook = self.workspace / "source.xlsx"
        document = self.workspace / "source.docx"
        write_xlsx(workbook, "workbook-v1")
        write_docx(document, "document-v1")
        state = self.begin_publish_run(
            "跨檔案整合", (workbook, document), mode="scheduled"
        )
        candidate = self.candidate_for(state)
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

    def test_cross_file_output_target_is_independent_of_source_order(self) -> None:
        workbook = self.workspace / "workbook.xlsx"
        document = self.workspace / "document.docx"
        write_xlsx(workbook, "workbook")
        write_docx(document, "document")
        task = "跨檔案固定輸出"

        first, _ = self.run_core(
            "needs-run",
            "--source",
            os.fspath(workbook),
            "--source",
            os.fspath(document),
            "--task",
            task,
            "--extension",
            ".xlsx",
        )
        second, _ = self.run_core(
            "needs-run",
            "--source",
            os.fspath(document),
            "--source",
            os.fspath(workbook),
            "--task",
            task,
            "--extension",
            ".xlsx",
        )
        self.assertEqual(first["target"], second["target"])

        state = self.begin_publish_run(task, (workbook, document), mode="scheduled")
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "combined")
        published, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(document),
            "--source",
            os.fspath(workbook),
            "--task",
            task,
            "--mode",
            "scheduled",
        )
        self.assertEqual(first["target"], published["target"])
        self.run_core("complete", "--summary", "published")

    def test_needs_run_does_not_create_an_output_directory(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")

        result, _ = self.run_core(
            "needs-run",
            "--source",
            os.fspath(source),
            "--task",
            "唯讀排程檢查",
            "--extension",
            ".xlsx",
        )
        self.assertTrue(result["needs_run"])
        self.assertFalse((self.workspace / "Office OS Output").exists())

    def test_publish_requires_one_manual_proposal_confirmation(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state, _ = self.run_core(
            "begin",
            "--task",
            "確認後發布",
            "--intent",
            "update",
            "--object",
            "office",
            "--permission",
            "fixed-output-write",
            "--qa",
            "fast",
            "--units",
            "1",
            "--source",
            os.fspath(source),
        )
        self.assertEqual(state["status"], "awaiting_confirmation")
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "candidate")

        denied, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "確認後發布",
            expected=2,
        )
        self.assertIn("confirmation", denied["error"])
        self.assertFalse((self.workspace / "Office OS Output").exists())

        confirmed, _ = self.run_core("confirm")
        self.assertTrue(confirmed["proposal_confirmed"])
        published, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "確認後發布",
        )
        self.assertEqual(published["status"], "published")

    def test_fixed_output_confirm_resumes_awaiting_user(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state, _ = self.run_core(
            "begin",
            "--task",
            "resume confirmation",
            "--intent",
            "update",
            "--object",
            "office",
            "--permission",
            "fixed-output-write",
            "--qa",
            "fast",
            "--units",
            "1",
            "--source",
            os.fspath(source),
        )
        self.assertEqual(state["status"], "awaiting_confirmation")

        waiting, _ = self.run_core("await-user")
        self.assertEqual(waiting["status"], "awaiting_user")
        self.assertFalse(waiting["proposal_confirmed"])

        confirmed, _ = self.run_core("confirm")
        self.assertEqual(confirmed["status"], "executing")
        self.assertTrue(confirmed["proposal_confirmed"])
        self.assertFalse(confirmed["waiting_for_user"])

    def test_publish_validation_rejects_shared_archive_limits_before_parse(self) -> None:
        module = load_core_module()
        candidate = self.workspace / "candidate.xlsx"
        write_xlsx(candidate, "compressed archive marker" * 256)
        limits = (
            ("archive size", "MAX_INDEX_PACKAGE_ARCHIVE_BYTES"),
            ("member count", "MAX_INDEX_PACKAGE_MEMBERS"),
            ("member size", "MAX_INDEX_PACKAGE_MEMBER_BYTES"),
            ("total size", "MAX_INDEX_PACKAGE_UNCOMPRESSED_BYTES"),
            ("compression ratio", "MAX_INDEX_PACKAGE_COMPRESSION_RATIO"),
        )

        for label, constant in limits:
            with self.subTest(limit=label):
                with mock.patch.object(module, constant, 1):
                    with mock.patch.object(
                        module.zipfile.ZipFile,
                        "testzip",
                        side_effect=AssertionError("testzip must not run"),
                    ) as testzip:
                        with mock.patch.object(
                            module,
                            "validate_openxml",
                            side_effect=AssertionError("Open XML parsing must not run"),
                        ) as validate_openxml:
                            with self.assertRaisesRegex(module.OfficeOSError, label):
                                module.validate_candidate(candidate)
                    testzip.assert_not_called()
                    validate_openxml.assert_not_called()

    def test_source_free_read_only_begin_requires_a_real_source(self) -> None:
        completed = self.run_core_raw(
            "begin",
            "--task",
            "read-only source required",
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

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--source", completed.stderr)
        self.assertFalse(self.plugin_data.exists())

    def test_publish_rejects_target_outside_fixed_output_directory(self) -> None:
        source = self.workspace / "source.xlsx"
        outside = self.workspace / "outside.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("固定位置", (source,))
        candidate = self.candidate_for(state)
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
        write_xlsx(source, "source-v1")
        state = self.begin_publish_run("來源保護", (source,))
        candidate = self.candidate_for(state)
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
        self.run_core("complete", "--summary", "baseline")

        state = self.begin_publish_run("來源保護", (source,))
        candidate = self.candidate_for(state)
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
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
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
            "--source",
            os.fspath(source),
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

    def test_complete_but_malformed_open_xml_cannot_replace_output(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("完整套件驗證", (source,))
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "good")
        published, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "完整套件驗證",
        )
        target = Path(published["target"])
        previous = target.read_bytes()
        self.run_core("complete", "--summary", "baseline")

        state = self.begin_publish_run("完整套件驗證", (source,))
        malformed = self.candidate_for(state, "malformed.xlsx")
        with zipfile.ZipFile(malformed, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            package.writestr("xl/workbook.xml", "not xml")
            package.writestr(
                "xl/_rels/workbook.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
            )
            package.writestr(
                "xl/worksheets/sheet1.xml",
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            )
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(malformed),
            "--source",
            os.fspath(source),
            "--task",
            "完整套件驗證",
            expected=2,
        )
        self.assertIn("XML", result["error"])
        self.assertEqual(target.read_bytes(), previous)
        self.run_core("fail", "--reason", "invalid package")

    def test_long_task_identities_and_stable_targets_do_not_collide(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        prefix = "季度報表" * 30
        tasks = (
            f"{prefix}-甲",
            f"{prefix}-乙",
            "Revenue / Cost",
            "Revenue \\ Cost",
        )
        states: list[dict] = []
        targets: list[Path] = []
        for index, task in enumerate(tasks):
            state = self.begin_publish_run(task, (source,))
            candidate = self.candidate_for(state)
            write_xlsx(candidate, f"output-{index}")
            states.append(state)
            published, _ = self.run_core(
                "publish",
                "--candidate",
                os.fspath(candidate),
                "--source",
                os.fspath(source),
                "--task",
                task,
            )
            targets.append(Path(published["target"]))
            self.run_core("complete", "--summary", f"task-{index}")
        self.assertEqual(len({state["task_key"] for state in states}), len(tasks))
        self.assertEqual(len(set(targets)), len(tasks))
        self.assertTrue(all(target.is_file() for target in targets))
        self.assertTrue(all(len(target.name.encode("utf-8")) <= 240 for target in targets))
        self.assertTrue(
            all(len(f"{target.name}.bak.3".encode("utf-8")) <= 255 for target in targets)
        )

        module = load_core_module()
        self.assertEqual(
            module.stable_task_key("Revenue / Cost"),
            module.stable_task_key("  REVENUE   /   COST  "),
        )
        self.assertEqual(
            module.safe_task_filename("Revenue / Cost"),
            module.safe_task_filename("  REVENUE   /   COST  "),
        )

        state = self.begin_publish_run(tasks[0], (source,))
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "alternate-attempt")
        alternate = targets[0].parent / "alternate.xlsx"
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            tasks[0],
            "--target",
            os.fspath(alternate),
            expected=2,
        )
        self.assertIn("stable target", result["error"])
        self.assertFalse(alternate.exists())
        self.run_core("fail", "--reason", "target mismatch")

    def test_query_rejects_unbounded_limit_and_text_sizes(self) -> None:
        cases = (
            ("--limit", "-1"),
            ("--limit", "101"),
            ("--max-chars", "0"),
            ("--max-chars", "8001"),
        )
        for option, value in cases:
            with self.subTest(option=option, value=value):
                completed = self.run_core_raw("query", "--text", "test", option, value)
                self.assertEqual(completed.returncode, 2)
                self.assertIn("out of range", completed.stderr)

    def test_plugin_data_and_candidates_are_never_indexed(self) -> None:
        self.plugin_data = self.workspace / ".office-os-plugin-data"
        candidate = self.plugin_data / "officecli-candidates" / "run" / "private.xlsx"
        normal = self.workspace / "normal.xlsx"
        write_xlsx(candidate, "candidate-secret")
        write_xlsx(normal, "normal-content")
        indexed, _ = self.run_core(
            "index",
            "--path",
            os.fspath(self.workspace),
            "--grant-full-text-root",
            os.fspath(self.workspace),
        )
        self.assertEqual(indexed["discovered"], 1)
        secret, _ = self.run_core("query", "--text", "candidate-secret")
        self.assertEqual(secret["count"], 0)

    def test_active_candidate_survives_cross_workspace_pruning(self) -> None:
        first_workspace = self.workspace
        source = first_workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("待修訂候選", (source,))
        candidate = self.candidate_for(state)
        candidate.write_text("invalid", encoding="utf-8")
        self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "待修訂候選",
            expected=2,
        )
        os.utime(candidate, (0, 0))

        second_workspace = self.base / "second-workspace"
        second_workspace.mkdir()
        self.workspace = second_workspace
        second_source = second_workspace / "second-source.xlsx"
        write_xlsx(second_source, "second-source")
        second, _ = self.run_core(
            "begin",
            "--task",
            "另一個工作區",
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
            "--source",
            os.fspath(second_source),
        )
        self.assertEqual(second["status"], "awaiting_confirmation")
        self.assertTrue(candidate.exists())
        self.run_core("complete", "--summary", "second complete")
        self.workspace = first_workspace
        self.run_core("fail", "--reason", "first cleanup")

    def test_workspace_retention_prunes_inactive_state_before_officecli_authority_limit(
        self,
    ) -> None:
        source = self.workspace / "active-source.xlsx"
        write_xlsx(source, "active-source")
        active = self.begin_publish_run("active authority", (source,))
        candidate = self.candidate_for(active)
        write_xlsx(candidate, "active-candidate")
        os.utime(candidate, (0, 0))
        active_state = self.workspace_data()
        workspaces = active_state.parent
        for number in range(512):
            (workspaces / f"inactive-{number:03d}").mkdir()

        second_workspace = self.base / "second-workspace"
        second_workspace.mkdir()
        self.workspace = second_workspace
        second_source = second_workspace / "second-source.xlsx"
        write_xlsx(second_source, "second-source")
        self.begin_publish_run("trigger retention", (second_source,))

        self.assertTrue((active_state / "run_state.json").is_file())
        self.assertTrue(candidate.is_file())
        self.assertLessEqual(len(list(workspaces.iterdir())), 256)

        script = (
            "const authority=require(process.argv[1]);"
            "try{process.stdout.write(JSON.stringify({run:authority.authorizeMutation(process.argv[2])}));}"
            "catch(error){process.stdout.write(JSON.stringify({error:error.message}));}"
        )
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        completed = subprocess.run(
            [
                shutil.which("node") or "node",
                "-e",
                script,
                os.fspath(AUTHORITY),
                os.fspath(candidate),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        authority = json.loads(completed.stdout)
        self.assertNotIn("error", authority)
        self.assertEqual(Path(authority["run"]["candidate"]), candidate.resolve())

    def test_workspace_retention_refuses_a_new_state_when_all_slots_are_active(
        self,
    ) -> None:
        # Given: all bounded workspace slots contain active runs, including this workspace.
        module = load_core_module()
        workspaces = self.plugin_data / "workspaces"
        workspaces.mkdir(parents=True)
        current_id = hashlib.sha256(
            module.canonical_workspace(self.workspace).encode("utf-8")
        ).hexdigest()[:24]
        active_state = json.dumps({"status": "executing"})
        for number in range(module.MAX_WORKSPACE_STATES):
            name = current_id if number == 0 else f"active-{number:03d}"
            directory = workspaces / name
            directory.mkdir()
            (directory / "run_state.json").write_text(active_state, encoding="utf-8")

        # When: the existing workspace is reopened, then a distinct workspace requests state.
        new_workspace = self.base / "new-workspace"
        new_workspace.mkdir()
        with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(self.plugin_data)}):
            reopened = module.get_workspace_dir(self.workspace)
            with self.assertRaisesRegex(
                module.OfficeOSError, "workspace state limit"
            ):
                module.get_workspace_dir(new_workspace)

        # Then: the active workspace remains usable and no 257th directory is created.
        self.assertEqual(reopened, workspaces / current_id)
        self.assertEqual(len(list(workspaces.iterdir())), module.MAX_WORKSPACE_STATES)
        new_id = hashlib.sha256(
            module.canonical_workspace(new_workspace).encode("utf-8")
        ).hexdigest()[:24]
        self.assertFalse((workspaces / new_id).exists())

    def test_begin_reserves_candidate_directory_before_first_publish(self) -> None:
        first_workspace = self.workspace
        source = first_workspace / "source.xlsx"
        write_xlsx(source, "source")
        first = self.begin_publish_run("撰寫中候選", (source,))
        candidate_directory = Path(first["candidate_directory"])
        candidate = candidate_directory / "candidate.xlsx"
        write_xlsx(candidate, "draft")
        os.utime(candidate, (0, 0))

        second_workspace = self.base / "second-workspace"
        second_workspace.mkdir()
        self.workspace = second_workspace
        second_source = second_workspace / "second-source.xlsx"
        write_xlsx(second_source, "second-source")
        self.begin_publish_run("另一工作區", (second_source,))
        self.assertTrue(candidate.is_file())
        self.run_core("complete", "--summary", "second complete")

        self.workspace = first_workspace
        self.run_core("fail", "--reason", "first cleanup")
        self.assertFalse(candidate_directory.exists())

    def test_publish_requires_reserved_directory_and_rejects_hard_links(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("候選授權", (source,))
        reserved = Path(state["candidate_directory"])
        sibling = reserved.parent / "other-run" / "candidate.xlsx"
        write_xlsx(sibling, "outside-reservation")

        outside_result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(sibling),
            "--source",
            os.fspath(source),
            "--task",
            "候選授權",
            expected=2,
        )
        self.assertIn("reserved", outside_result["error"])
        active = json.loads(
            (self.workspace_data() / "run_state.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(active["candidate"])

        outside = self.base / "outside-candidate.xlsx"
        write_xlsx(outside, "hard-linked")
        linked = reserved / "candidate.xlsx"
        os.link(outside, linked)
        before = hashlib.sha256(outside.read_bytes()).hexdigest()
        hardlink_result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(linked),
            "--source",
            os.fspath(source),
            "--task",
            "候選授權",
            expected=2,
        )
        self.assertIn("hard", hardlink_result["error"])
        self.assertEqual(hashlib.sha256(outside.read_bytes()).hexdigest(), before)
        self.assertFalse((self.workspace / "Office OS Output").exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows extended paths")
    def test_publish_accepts_extended_path_for_its_reserved_candidate_directory(
        self,
    ) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        begun = self.begin_publish_run("extended candidate path", (source,))
        candidate = Path(begun["candidate_directory"]) / "candidate.xlsx"
        write_xlsx(candidate, "candidate")
        state_path = self.workspace_data() / "run_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["candidate_directory"] = "\\\\?\\" + state["candidate_directory"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        candidate = Path(state["candidate_directory"]) / "candidate.xlsx"

        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "extended candidate path",
        )

        self.assertEqual(result["status"], "published")
        self.assertTrue(Path(result["target"]).is_file())
        self.assertTrue(result["candidate_removed"])
        self.assertFalse(candidate.exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_publish_accepts_short_path_for_its_reserved_candidate_directory(
        self,
    ) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        begun = self.begin_publish_run("short candidate path", (source,))
        candidate = Path(begun["candidate_directory"]) / "candidate.xlsx"
        write_xlsx(candidate, "candidate")
        state_path = self.workspace_data() / "run_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["candidate_directory"] = os.fspath(
            short_windows_path(Path(state["candidate_directory"]))
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        candidate = Path(state["candidate_directory"]) / "candidate.xlsx"

        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "short candidate path",
        )

        self.assertEqual(result["status"], "published")
        self.assertTrue(Path(result["target"]).is_file())
        self.assertTrue(result["candidate_removed"])
        self.assertFalse(candidate.exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_publish_rejects_short_path_for_another_candidate_directory(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("short outside candidate", (source,))
        candidate = Path(state["candidate_directory"]) / "candidate.xlsx"
        write_xlsx(candidate, "candidate")
        outside = self.base / "outside-candidate-directory"
        outside.mkdir()
        state["candidate_directory"] = os.fspath(short_windows_path(outside))
        (self.workspace_data() / "run_state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "short outside candidate",
            expected=2,
        )

        self.assertIn("reserved", result["error"])
        self.assertTrue(candidate.is_file())

    def test_candidate_cleanup_fails_closed_on_semantically_invalid_active_state(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("語意損壞狀態", (source,))
        candidate_directory = Path(state["candidate_directory"])
        candidate = candidate_directory / "candidate.xlsx"
        write_xlsx(candidate, "draft")
        state_path = self.workspace_data() / "run_state.json"
        cases = (
            {**state, "candidate_directory": 42},
            {key: value for key, value in state.items() if key != "candidate_directory"},
            {**state, "candidate_directory": os.fspath(self.base / "outside")},
            {**state, "candidate": 42},
        )
        for malformed in cases:
            with self.subTest(malformed=malformed):
                state_path.write_text(json.dumps(malformed), encoding="utf-8")
                result, _ = self.run_core(
                    "cleanup", "--older-than-seconds", "0", expected=2
                )
                self.assertIn("active", result["error"])
                self.assertTrue(candidate.is_file())

    def test_candidate_cleanup_fails_closed_on_malformed_active_inventory(self) -> None:
        first_workspace = self.workspace
        source = first_workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("損壞狀態", (source,))
        candidate = self.candidate_for(state)
        write_xlsx(candidate, "draft")
        os.utime(candidate, (0, 0))
        (self.workspace_data() / "run_state.json").write_text("{", encoding="utf-8")

        second_workspace = self.base / "second-workspace"
        second_workspace.mkdir()
        self.workspace = second_workspace
        second_source = second_workspace / "second-source.xlsx"
        write_xlsx(second_source, "second-source")
        result, _ = self.run_core(
            "begin",
            "--task",
            "第二工作區",
            "--intent",
            "update",
            "--object",
            "office",
            "--permission",
            "fixed-output-write",
            "--qa",
            "fast",
            "--units",
            "1",
            "--source",
            os.fspath(second_source),
            expected=2,
        )
        self.assertIn("run state", result["error"])
        self.assertTrue(candidate.is_file())
        self.workspace = first_workspace

    def test_publish_rejects_linked_output_directory(self) -> None:
        source = self.workspace / "source.xlsx"
        outside = self.base / "outside-output"
        outside.mkdir()
        write_xlsx(source, "source")
        output = self.workspace / "Office OS Output"
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(output), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            output.symlink_to(outside, target_is_directory=True)
        try:
            state = self.begin_publish_run("輸出邊界", (source,))
            candidate = self.candidate_for(state)
            write_xlsx(candidate, "candidate")
            result, _ = self.run_core(
                "publish",
                "--candidate",
                os.fspath(candidate),
                "--source",
                os.fspath(source),
                "--task",
                "輸出邊界",
                expected=2,
            )
            self.assertIn("linked", result["error"])
            self.assertEqual(list(outside.iterdir()), [])
            self.run_core("fail", "--reason", "linked output")
        finally:
            if os.path.lexists(output):
                if os.name == "nt":
                    os.rmdir(output)
                else:
                    output.unlink()

    def test_cleanup_rejects_linked_output_directory_without_following_it(self) -> None:
        outside = self.base / "outside-output"
        outside.mkdir()
        sentinel = outside / ".office-os-old.tmp"
        sentinel.write_text("outside", encoding="utf-8")
        os.utime(sentinel, (0, 0))
        output = self.workspace / "Office OS Output"
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(output), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            output.symlink_to(outside, target_is_directory=True)
        try:
            result, _ = self.run_core(
                "cleanup",
                "--path",
                os.fspath(self.workspace),
                "--older-than-seconds",
                "0",
                expected=2,
            )
            self.assertIn("linked", result["error"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
        finally:
            if os.path.lexists(output):
                if os.name == "nt":
                    os.rmdir(output)
                else:
                    output.unlink()

    def test_cleanup_rejects_outside_and_linked_ancestor_roots(self) -> None:
        outside = self.base / "outside-cleanup"
        outside.mkdir()
        temporary = outside / "Office OS Output" / ".office-os-temp.xlsx"
        temporary.parent.mkdir()
        temporary.write_text("outside", encoding="utf-8")

        outside_result, _ = self.run_core(
            "cleanup",
            "--path",
            os.fspath(outside),
            "--older-than-seconds",
            "0",
            expected=2,
        )
        self.assertIn("workspace", outside_result["error"])
        self.assertEqual(temporary.read_text(encoding="utf-8"), "outside")

        linked = self.workspace / "linked-cleanup"
        if os.name == "nt":
            linked_result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked_result.returncode, 0, linked_result.stderr)
        else:
            linked.symlink_to(outside, target_is_directory=True)
        try:
            linked_result, _ = self.run_core(
                "cleanup",
                "--path",
                os.fspath(linked),
                "--older-than-seconds",
                "0",
                expected=2,
            )
            self.assertIn("linked", linked_result["error"])
            self.assertEqual(temporary.read_text(encoding="utf-8"), "outside")
        finally:
            if os.path.lexists(linked):
                if os.name == "nt":
                    os.rmdir(linked)
                else:
                    linked.unlink()

    def test_core_rejects_linked_workspace_state_ancestors(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        self.plugin_data.mkdir()
        outside = self.base / "outside-workspaces"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        workspaces = self.plugin_data / "workspaces"
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(workspaces), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            workspaces.symlink_to(outside, target_is_directory=True)
        try:
            result, _ = self.run_core(
                "begin",
                "--task",
                "連結工作區狀態",
                "--intent",
                "update",
                "--object",
                "office",
                "--permission",
                "fixed-output-write",
                "--qa",
                "fast",
                "--units",
                "1",
                "--source",
                os.fspath(source),
                expected=2,
            )
            self.assertIn("linked", result["error"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
            self.assertEqual(list(outside.iterdir()), [sentinel])
        finally:
            if os.path.lexists(workspaces):
                if os.name == "nt":
                    os.rmdir(workspaces)
                else:
                    workspaces.unlink()

    def test_open_xml_relationship_targets_must_exist(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        state = self.begin_publish_run("關聯驗證", (source,))
        candidate = self.candidate_for(state, "missing-relationship.xlsx")
        with zipfile.ZipFile(candidate, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/other.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>",
            )
            package.writestr(
                "_rels/.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="xl/workbook.xml"/>'
                "</Relationships>",
            )
            package.writestr(
                "xl/workbook.xml",
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            )
            package.writestr(
                "xl/_rels/workbook.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/missing.xml" />'
                "</Relationships>",
            )
            package.writestr(
                "xl/worksheets/other.xml",
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            )
        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "關聯驗證",
            expected=2,
        )
        self.assertIn("relationship target is missing", result["error"])
        self.run_core("fail", "--reason", "missing relationship")

    def test_core_rejects_linked_plugin_data_before_state_write(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        real_data = self.base / "real-plugin-data"
        real_data.mkdir()
        sentinel = real_data / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        linked_data = self.base / "linked-plugin-data"
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(linked_data), os.fspath(real_data)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            linked_data.symlink_to(real_data, target_is_directory=True)
        self.plugin_data = linked_data
        try:
            result, _ = self.run_core(
                "begin",
                "--task",
                "連結資料根",
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
                "--source",
                os.fspath(source),
                expected=2,
            )
            self.assertIn("linked", result["error"])
            self.assertFalse((real_data / "workspaces").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
        finally:
            if os.path.lexists(linked_data):
                if os.name == "nt":
                    os.rmdir(linked_data)
                else:
                    linked_data.unlink()

    def test_core_rejects_linked_plugin_data_ancestor_before_state_write(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        outside = self.base / "outside-ancestor"
        plugin_data = outside / "plugin-data"
        plugin_data.mkdir(parents=True)
        sentinel = plugin_data / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        linked_parent = self.base / "linked-parent"
        if os.name == "nt":
            linked = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(linked_parent), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linked.returncode, 0, linked.stderr)
        else:
            linked_parent.symlink_to(outside, target_is_directory=True)
        self.plugin_data = linked_parent / "plugin-data"
        try:
            result, _ = self.run_core(
                "begin",
                "--task",
                "連結資料祖先",
                "--intent",
                "update",
                "--object",
                "office",
                "--permission",
                "fixed-output-write",
                "--qa",
                "fast",
                "--units",
                "1",
                "--source",
                os.fspath(source),
                expected=2,
            )
            self.assertIn("linked", result["error"])
            self.assertFalse((plugin_data / "workspaces").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
        finally:
            if os.path.lexists(linked_parent):
                if os.name == "nt":
                    os.rmdir(linked_parent)
                else:
                    linked_parent.unlink()

    def test_state_lock_does_not_take_over_a_live_owner_with_old_mtime(self) -> None:
        module = load_core_module()
        lock_directory = self.base / "locks"
        lock_directory.mkdir()
        first = module.state_lock(lock_directory, timeout=0.1)
        first.__enter__()
        try:
            lock_path = lock_directory / "run-state.lock"
            os.utime(lock_path, (0, 0))
            with self.assertRaises(module.OfficeOSError):
                with module.state_lock(lock_directory, timeout=0.05):
                    pass
        finally:
            first.__exit__(None, None, None)

        with module.state_lock(lock_directory, timeout=0.1):
            self.assertTrue((lock_directory / "run-state.lock").is_file())

    def test_state_lock_recovers_after_owner_process_terminates(self) -> None:
        module = load_core_module()
        lock_directory = self.base / "locks"
        lock_directory.mkdir()
        ready = self.base / "lock-ready"
        script = (
            "import importlib.util, os, sys, time\n"
            "from pathlib import Path\n"
            "core=Path(sys.argv[1]); sys.path.insert(0, os.fspath(core.parent))\n"
            "spec=importlib.util.spec_from_file_location('office_os_lock_child', core)\n"
            "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module\n"
            "spec.loader.exec_module(module)\n"
            "with module.state_lock(Path(sys.argv[2]), timeout=1.0):\n"
            "    Path(sys.argv[3]).write_text('ready', encoding='ascii')\n"
            "    time.sleep(30)\n"
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                os.fspath(CORE),
                os.fspath(lock_directory),
                os.fspath(ready),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + 5.0
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            self.assertTrue(ready.is_file(), f"lock child exited with {process.poll()}")
            with self.assertRaises(module.OfficeOSError):
                with module.state_lock(lock_directory, timeout=0.05):
                    pass
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

        with module.state_lock(lock_directory, timeout=0.1):
            self.assertTrue((lock_directory / "run-state.lock").is_file())

    def test_core_state_hardlinks_are_refused_without_mutating_sentinels(self) -> None:
        module = load_core_module()

        def enter_lock(directory: Path) -> None:
            with module.state_lock(directory, timeout=0.05):
                pass

        def write_state(directory: Path) -> None:
            module.write_json(directory / "run_state.json", {"status": "executing"})

        def open_database(directory: Path) -> None:
            connection = module.connect_database(directory)
            connection.close()

        def acquire_single_flight(directory: Path) -> None:
            module.acquire_single_flight(directory, "run-id", "task-key")

        cases = (
            ("run-state.lock", b"outside lock sentinel", enter_lock),
            ("run_state.json", b"outside state sentinel", write_state),
            ("office.db", b"", open_database),
            ("office.db-wal", b"outside WAL sentinel", open_database),
            ("office.db-shm", b"outside SHM sentinel", open_database),
            ("office.db-journal", b"outside journal sentinel", open_database),
            ("single-flight.lock", b"outside schedule sentinel", acquire_single_flight),
        )
        for name, contents, action in cases:
            with self.subTest(name=name):
                directory = self.base / f"hardlink-{name}"
                directory.mkdir()
                sentinel = self.base / f"outside-{name}"
                sentinel.write_bytes(contents)
                os.link(sentinel, directory / name)
                before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
                with self.assertRaises(module.OfficeOSError):
                    action(directory)
                self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

        directory = self.base / "hardlink-temporary"
        directory.mkdir()
        sentinel = self.base / "outside-temporary"
        sentinel.write_bytes(b"outside temporary sentinel")
        temporary = directory / f".office-os-run_state.json.{os.getpid()}.tmp"
        os.link(sentinel, temporary)
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        with self.assertRaises(module.OfficeOSError):
            write_state(directory)
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_committed_publish_reports_success_when_candidate_cleanup_fails(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        begun = self.begin_publish_run("提交後清理", (source,))
        candidate = self.candidate_for(begun)
        write_xlsx(candidate, "candidate")
        directory = self.workspace_data()
        module = load_core_module()
        messages = []
        arguments = SimpleNamespace(
            candidate=os.fspath(candidate),
            source=[os.fspath(source)],
            task="提交後清理",
            mode="manual",
            target=None,
            cwd=os.fspath(self.workspace),
        )
        with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(self.plugin_data)}):
            with mock.patch.object(
                module,
                "remove_managed_candidate",
                side_effect=module.CandidateLifecycleError("synthetic cleanup failure"),
            ):
                with mock.patch.object(module, "json_print", side_effect=messages.append):
                    self.assertEqual(module.publish_candidate(arguments, directory), 0)
        result = messages[-1]
        self.assertEqual(result["status"], "published")
        self.assertIn("synthetic cleanup failure", result["candidate_cleanup_error"])
        self.assertTrue(Path(result["target"]).is_file())
        state = json.loads((directory / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["candidate"], os.fspath(candidate))
        self.assertTrue(os.path.samefile(state["candidate"], candidate))
        self.assertIsNotNone(state["candidate_directory"])
        self.run_core("complete", "--summary", "published with deferred cleanup")

    def test_committed_publish_ignores_malformed_prior_publish_record(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        begun = self.begin_publish_run("提交後毀損紀錄", (source,))
        candidate = self.candidate_for(begun)
        write_xlsx(candidate, "candidate")
        directory = self.workspace_data()
        (directory / "publish_state.json").write_text(
            json.dumps({"tasks": {"corrupt": {"target": 1}}}), encoding="utf-8"
        )

        result, _ = self.run_core(
            "publish",
            "--candidate",
            os.fspath(candidate),
            "--source",
            os.fspath(source),
            "--task",
            "提交後毀損紀錄",
        )

        self.assertEqual(result["status"], "published")
        self.assertTrue(Path(result["target"]).is_file())
        records = json.loads((directory / "publish_state.json").read_text(encoding="utf-8"))
        self.assertNotIn("corrupt", records["tasks"])

    def test_committed_publish_reports_success_when_final_state_write_fails(self) -> None:
        source = self.workspace / "source.xlsx"
        write_xlsx(source, "source")
        begun = self.begin_publish_run("提交後狀態", (source,))
        candidate = Path(begun["candidate_directory"]) / "candidate.xlsx"
        write_xlsx(candidate, "candidate")
        directory = self.workspace_data()
        module = load_core_module()
        messages: list[dict] = []
        arguments = SimpleNamespace(
            candidate=os.fspath(candidate),
            source=[os.fspath(source)],
            task="提交後狀態",
            mode="manual",
            target=None,
            cwd=os.fspath(self.workspace),
        )
        original_write_json = module.write_json

        def fail_final_state_write(path: Path, value) -> None:
            if path == module.run_state_path(directory) and isinstance(value, dict):
                if value.get("candidate") is None:
                    raise OSError("synthetic final state failure")
            original_write_json(path, value)

        with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(self.plugin_data)}):
            with mock.patch.object(module, "write_json", side_effect=fail_final_state_write):
                with mock.patch.object(module, "json_print", side_effect=messages.append):
                    self.assertEqual(module.publish_candidate(arguments, directory), 0)
        result = messages[-1]
        self.assertEqual(result["status"], "published")
        self.assertTrue(Path(result["target"]).is_file())
        self.assertTrue(
            any("synthetic final state failure" in item for item in result["post_commit_errors"])
        )

if __name__ == "__main__":
    unittest.main()

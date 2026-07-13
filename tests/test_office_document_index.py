# noqa: SIZE_OK - Focused Open XML fixtures keep the extraction contract self-contained.
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "office-os" / "scripts"
CORE = SCRIPTS / "office_os.py"
LEAF = SCRIPTS / "office_document_index.py"


class OfficeDocumentIndexTestSetupError(RuntimeError):
    pass


def load_sibling_module(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise OfficeDocumentIndexTestSetupError(f"Unable to load {path.name} for behavioral test.")
    module = importlib.util.module_from_spec(spec)
    scripts = os.fspath(path.parent)
    sys.path.insert(0, scripts)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def load_document_index_module() -> ModuleType:
    if not LEAF.is_file():
        raise OfficeDocumentIndexTestSetupError(
            "office_document_index.py leaf is absent; document indexing has not been extracted yet."
        )
    return load_sibling_module("office_document_index", LEAF)


def load_core_module() -> ModuleType:
    return load_sibling_module("office_os_document_index_test", CORE)


def write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in members.items():
            package.writestr(name, content)


def expected_chunk(ordinal: int, locator: str, heading: str, text: str) -> dict[str, str | int]:
    return {
        "ordinal": ordinal,
        "locator": locator,
        "heading": heading,
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def write_docx_fixture(path: Path) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    write_zip(
        path,
        {
            "word/header10.xml": (
                f'<w:hdr xmlns:w="{word}"><w:p><w:r><w:t>Header ten</w:t></w:r></w:p></w:hdr>'
            ),
            "word/document.xml": f"""<w:document xmlns:w="{word}">
              <w:body>
                <w:p><w:r><w:t>Preface</w:t></w:r></w:p>
                <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Plan</w:t></w:r></w:p>
                <w:p><w:r><w:t>Body text</w:t></w:r></w:p>
                <w:tbl>
                  <w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Cost</w:t></w:r></w:p></w:tc></w:tr>
                  <w:tr><w:tc><w:p><w:r><w:t>Desk</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr>
                </w:tbl>
                <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Next</w:t></w:r></w:p>
                <w:p><w:r><w:t>Follow up</w:t></w:r></w:p>
              </w:body>
            </w:document>""",
            "word/header2.xml": (
                f'<w:hdr xmlns:w="{word}"><w:p><w:r><w:t>Header two</w:t></w:r></w:p></w:hdr>'
            ),
        },
    )


def write_pptx_fixture(path: Path) -> None:
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    presentation = "http://schemas.openxmlformats.org/presentationml/2006/main"

    def slide(title: str, body: str) -> str:
        return (
            f'<p:sld xmlns:p="{presentation}" xmlns:a="{drawing}"><p:cSld><p:spTree>'
            f'<p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p>'
            f'<a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:txBody></p:sp>'
            "</p:spTree></p:cSld></p:sld>"
        )

    def notes(text: str) -> str:
        return f'<p:notes xmlns:p="{presentation}" xmlns:a="{drawing}"><a:t>{text}</a:t></p:notes>'

    write_zip(
        path,
        {
            "ppt/slides/slide10.xml": slide("Ten title", "Ten body"),
            "ppt/notesSlides/notesSlide10.xml": notes("Ten note"),
            "ppt/slides/slide2.xml": slide("Two title", "Two body"),
            "ppt/notesSlides/notesSlide2.xml": notes("Two note"),
        },
    )


def write_relationship_ordered_pptx_fixture(path: Path) -> None:
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    presentation = "http://schemas.openxmlformats.org/presentationml/2006/main"
    relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    office_relationships = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )

    def slide(title: str, body: str) -> str:
        return (
            f'<p:sld xmlns:p="{presentation}" xmlns:a="{drawing}"><p:cSld><p:spTree>'
            f'<p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p>'
            f'<a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:txBody></p:sp>'
            "</p:spTree></p:cSld></p:sld>"
        )

    def notes(text: str) -> str:
        return f'<p:notes xmlns:p="{presentation}" xmlns:a="{drawing}"><a:t>{text}</a:t></p:notes>'

    write_zip(
        path,
        {
            "ppt/presentation.xml": (
                f'<p:presentation xmlns:p="{presentation}" xmlns:r="{office_relationships}">'
                '<p:sldIdLst><p:sldId id="256" r:id="rId2"/>'
                '<p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>'
            ),
            "ppt/_rels/presentation.xml.rels": (
                f'<Relationships xmlns="{relationships}">'
                f'<Relationship Id="rId1" Type="{office_relationships}/slide" '
                'Target="slides/slide1.xml"/>'
                f'<Relationship Id="rId2" Type="{office_relationships}/slide" '
                'Target="slides/slide2.xml"/></Relationships>'
            ),
            "ppt/slides/slide1.xml": slide("One title", "One body"),
            "ppt/slides/_rels/slide1.xml.rels": (
                f'<Relationships xmlns="{relationships}">'
                f'<Relationship Id="rId9" Type="{office_relationships}/notesSlide" '
                'Target="../notesSlides/notesSlide2.xml"/></Relationships>'
            ),
            "ppt/slides/slide2.xml": slide("Two title", "Two body"),
            "ppt/slides/_rels/slide2.xml.rels": (
                f'<Relationships xmlns="{relationships}">'
                f'<Relationship Id="rId8" Type="{office_relationships}/notesSlide" '
                'Target="../notesSlides/notesSlide1.xml"/></Relationships>'
            ),
            "ppt/notesSlides/notesSlide1.xml": notes("Two note"),
            "ppt/notesSlides/notesSlide2.xml": notes("One note"),
        },
    )


def write_xlsx_fixture(path: Path) -> None:
    spreadsheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    office_relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    write_zip(
        path,
        {
            "xl/tables/table10.xml": (
                f'<table xmlns="{spreadsheet}" displayName="Metrics10" ref="C1:D2">'
                '<tableColumns count="2"><tableColumn id="1" name="Late"/>'
                '<tableColumn id="2" name="Value"/></tableColumns></table>'
            ),
            "xl/worksheets/sheet1.xml": f"""<worksheet xmlns="{spreadsheet}">
              <sheetData><row r="2"><c r="F2" t="s"><v>0</v></c></row></sheetData>
            </worksheet>""",
            "xl/tables/table2.xml": (
                f'<table xmlns="{spreadsheet}" displayName="Metrics2" ref="A1:B2">'
                '<tableColumns count="2"><tableColumn id="1" name="Item"/>'
                '<tableColumn id="2" name="Amount"/></tableColumns></table>'
            ),
            "xl/sharedStrings.xml": f"""<sst xmlns="{spreadsheet}">
              <si><r><t>Rich</t></r><r><t> value</t></r></si>
            </sst>""",
            "xl/workbook.xml": f"""<workbook xmlns="{spreadsheet}" xmlns:r="{office_relationships}">
              <sheets><sheet name="Ledger" sheetId="2" r:id="rId2"/>
              <sheet name="Archive" sheetId="1" r:id="rId1"/></sheets>
            </workbook>""",
            "xl/_rels/workbook.xml.rels": f"""<Relationships xmlns="{relationships}">
              <Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/>
              <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
            </Relationships>""",
            "xl/worksheets/sheet2.xml": f"""<worksheet xmlns="{spreadsheet}">
              <sheetData><row r="1">
                <c r="A1" t="s"><v>0</v></c>
                <c r="B1" t="inlineStr"><is><t>Inline value</t></is></c>
                <c r="C1"><f>SUM(A2:A3)</f><v>42</v></c>
                <c r="D1" t="b"><v>1</v></c>
                <c r="E1" t="b"><v>0</v></c>
              </row></sheetData>
            </worksheet>""",
        },
    )


def permissive_limits(module: ModuleType):
    return module.IndexPackageLimits(
        max_archive_bytes=1_000_000,
        max_members=100,
        max_member_bytes=1_000_000,
        max_uncompressed_bytes=1_000_000,
        max_compression_ratio=100,
    )


class OfficeDocumentIndexCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_pptx_preserves_natural_slide_order_and_numeric_notes(self) -> None:
        presentation = self.base / "ordered.pptx"
        write_pptx_fixture(presentation)

        leaf = load_document_index_module()

        self.assertEqual(
            leaf.extract_pptx(presentation, permissive_limits(leaf)),
            [
                expected_chunk(
                    0,
                    "slide=2;shape-tree=all",
                    "Two title",
                    "Two title\nTwo body\n\nNotes:\nTwo note",
                ),
                expected_chunk(
                    1,
                    "slide=10;shape-tree=all",
                    "Ten title",
                    "Ten title\nTen body\n\nNotes:\nTen note",
                ),
            ],
        )

    def test_pptx_follows_presentation_order_and_notes_relationships(self) -> None:
        presentation = self.base / "relationship-ordered.pptx"
        write_relationship_ordered_pptx_fixture(presentation)

        leaf = load_document_index_module()

        self.assertEqual(
            leaf.extract_pptx(presentation, permissive_limits(leaf)),
            [
                expected_chunk(
                    0,
                    "slide=2;shape-tree=all",
                    "Two title",
                    "Two title\nTwo body\n\nNotes:\nTwo note",
                ),
                expected_chunk(
                    1,
                    "slide=1;shape-tree=all",
                    "One title",
                    "One title\nOne body\n\nNotes:\nOne note",
                ),
            ],
        )

    def test_docx_preserves_semantic_sections_tables_and_auxiliary_parts(self) -> None:
        document = self.base / "semantic.docx"
        write_docx_fixture(document)

        leaf = load_document_index_module()

        self.assertEqual(
            leaf.extract_docx(document, permissive_limits(leaf)),
            [
                expected_chunk(0, "heading=Document;part=1", "Document", "Preface"),
                expected_chunk(
                    1,
                    "heading=Plan;part=1",
                    "Plan",
                    "Plan\n\nBody text\n\nItem\tCost\nDesk\t100",
                ),
                expected_chunk(2, "heading=Next;part=1", "Next", "Next\n\nFollow up"),
                expected_chunk(3, "part=word/header2.xml", "header2", "Header two"),
                expected_chunk(4, "part=word/header10.xml", "header10", "Header ten"),
            ],
        )

    def test_docx_splits_an_oversized_paragraph_to_the_character_limit(self) -> None:
        document = self.base / "oversized-paragraph.docx"
        word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        paragraph = "x" * 12_001
        write_zip(
            document,
            {
                "word/document.xml": (
                    f'<w:document xmlns:w="{word}"><w:body><w:p><w:r><w:t>'
                    f"{paragraph}"
                    "</w:t></w:r></w:p></w:body></w:document>"
                )
            },
        )

        leaf = load_document_index_module()
        chunks = leaf.extract_docx(document, permissive_limits(leaf))

        self.assertEqual(
            chunks,
            [
                expected_chunk(
                    0,
                    "heading=Document;part=1",
                    "Document",
                    "x" * 12_000,
                ),
                expected_chunk(1, "heading=Document;part=2", "Document", "x"),
            ],
        )
        self.assertTrue(all(len(item["text"]) <= 12_000 for item in chunks))

    def test_split_text_chunks_counts_paragraph_separators_against_the_limit(self) -> None:
        leaf = load_document_index_module()

        chunks = leaf.split_text_chunks(
            "aaaaa\n\nbbbbb\n\nccc",
            "heading=Document",
            "Document",
            0,
            max_chars=10,
        )

        self.assertEqual(
            chunks,
            [
                expected_chunk(0, "heading=Document;part=1", "Document", "aaaaa"),
                expected_chunk(
                    1,
                    "heading=Document;part=2",
                    "Document",
                    "bbbbb\n\nccc",
                ),
            ],
        )
        self.assertTrue(all(len(item["text"]) <= 10 for item in chunks))

    def test_xlsx_resolves_relationships_and_shared_strings(self) -> None:
        workbook = self.base / "relationships.xlsx"
        write_xlsx_fixture(workbook)

        leaf = load_document_index_module()

        self.assertEqual(
            leaf.extract_xlsx(workbook, permissive_limits(leaf)),
            [
                expected_chunk(
                    0,
                    "sheet=Ledger;range=A1:E1;kind=cells",
                    "Ledger",
                    "A1\tRich value\nB1\tInline value\nC1\t=SUM(A2:A3)\t→ 42\nD1\tTRUE\nE1\tFALSE",
                ),
                expected_chunk(1, "sheet=Archive;range=F2:F2;kind=cells", "Archive", "F2\tRich value"),
                expected_chunk(2, "table=Metrics2;range=A1:B2", "Metrics2", "Item\tAmount"),
                expected_chunk(3, "table=Metrics10;range=C1:D2", "Metrics10", "Late\tValue"),
            ],
        )

    def test_core_rejects_a_compressed_docx_from_public_dispatch(self) -> None:
        document = self.base / "compressed.docx"
        word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        write_zip(
            document,
            {
                "word/document.xml": (
                    f'<w:document xmlns:w="{word}"><w:body><w:p><w:r><w:t>'
                    f'{"x" * 200_000}'
                    "</w:t></w:r></w:p></w:body></w:document>"
                )
            },
        )

        core = load_core_module()

        with self.assertRaises(core.OfficeOSError) as package_limit:
            core.extract_chunks(document)

        self.assertEqual(
            str(package_limit.exception),
            "Office package exceeds index limits: compression ratio.",
        )

    def test_core_reports_malformed_docx_content(self) -> None:
        document = self.base / "malformed.docx"
        write_zip(document, {"word/document.xml": "<document>"})

        core = load_core_module()

        with self.assertRaises(core.OfficeOSError) as malformed_error:
            core.extract_chunks(document)

        self.assertRegex(
            str(malformed_error.exception),
            r"^Could not extract \.docx content: ",
        )

    def test_core_reports_malformed_pptx_content(self) -> None:
        presentation = self.base / "malformed.pptx"
        write_zip(presentation, {"ppt/slides/slide1.xml": "<slide>"})

        core = load_core_module()

        with self.assertRaises(core.OfficeOSError) as malformed_error:
            core.extract_chunks(presentation)

        self.assertRegex(
            str(malformed_error.exception),
            r"^Could not extract \.pptx content: ",
        )

    def test_core_reports_malformed_xlsx_content(self) -> None:
        workbook = self.base / "malformed.xlsx"
        write_zip(workbook, {"xl/workbook.xml": "<workbook>"})

        core = load_core_module()

        with self.assertRaises(core.OfficeOSError) as malformed_error:
            core.extract_chunks(workbook)

        self.assertRegex(
            str(malformed_error.exception),
            r"^Could not extract \.xlsx content: ",
        )

    def test_core_requires_conversion_for_pdf_content(self) -> None:
        core = load_core_module()

        with self.assertRaises(core.OfficeOSError) as unsupported:
            core.extract_chunks(self.base / "conversion.pdf")

        self.assertEqual(
            str(unsupported.exception),
            "Content extraction requires conversion for .pdf.",
        )

    def test_core_cli_requires_authoritative_plugin_data(self) -> None:
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment.pop("CLAUDE_PLUGIN_DATA", None)

        completed = subprocess.run(
            [sys.executable, os.fspath(CORE), "status", "--cwd", os.fspath(self.base)],
            cwd=self.base,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertIn("PLUGIN_DATA", payload["error"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "office-os" / "scripts"
sys.path.insert(0, os.fspath(SCRIPTS))
try:
    from office_openxml import OpenXMLValidationError, validate_openxml
finally:
    sys.path.remove(os.fspath(SCRIPTS))


class OpenXMLValidationCase(unittest.TestCase):
    def validate_parts(self, parts: dict[str, str], extension: str) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
            for name, content in parts.items():
                package.writestr(name, content)
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as package:
            validate_openxml(package, extension)

    def test_rejects_namespace_less_xlsx_roots(self) -> None:
        parts = {
            "[Content_Types].xml": "<Types/>",
            "xl/workbook.xml": "<workbook/>",
            "xl/_rels/workbook.xml.rels": (
                "<Relationships>"
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
            "xl/worksheets/sheet1.xml": "<worksheet/>",
        }

        with self.assertRaisesRegex(OpenXMLValidationError, "XML root"):
            self.validate_parts(parts, ".xlsx")

    def test_rejects_empty_content_type_declarations(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types"/>'
            ),
            "xl/workbook.xml": (
                '<workbook xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"/>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"/>'
            ),
        }

        with self.assertRaisesRegex(OpenXMLValidationError, "content type"):
            self.validate_parts(parts, ".xlsx")

    def test_docx_requires_package_relationship_to_main_document(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types">'
                '<Default Extension="xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body/></w:document>'
            ),
        }

        with self.assertRaisesRegex(
            OpenXMLValidationError, "officeDocument relationship"
        ):
            self.validate_parts(parts, ".docx")

    def test_xlsx_requires_workbook_relationship_to_existing_worksheet(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types">'
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'spreadsheetml.worksheet+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/officeDocument" Target="xl/workbook.xml"/>'
                "</Relationships>"
            ),
            "xl/workbook.xml": (
                '<workbook xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"/>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships"/>'
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main"><sheetData/></worksheet>'
            ),
        }

        with self.assertRaisesRegex(OpenXMLValidationError, "worksheet relationship"):
            self.validate_parts(parts, ".xlsx")

    def test_pptx_requires_presentation_relationship_to_existing_slide(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types">'
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/ppt/presentation.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'presentationml.presentation.main+xml"/>'
                '<Override PartName="/ppt/slides/slide1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'presentationml.slide+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/officeDocument" Target="ppt/presentation.xml"/>'
                "</Relationships>"
            ),
            "ppt/presentation.xml": (
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main"/>'
            ),
            "ppt/_rels/presentation.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships"/>'
            ),
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main"><p:cSld/></p:sld>'
            ),
        }

        with self.assertRaisesRegex(OpenXMLValidationError, "slide relationship"):
            self.validate_parts(parts, ".pptx")

    def test_rejects_correct_local_names_in_wrong_namespaces(self) -> None:
        parts = {
            "[Content_Types].xml": "<Types xmlns=\"urn:not-opc\"/>",
            "xl/workbook.xml": "<workbook xmlns=\"urn:not-spreadsheetml\"/>",
            "xl/_rels/workbook.xml.rels": "<Relationships xmlns=\"urn:not-opc\"/>",
            "xl/worksheets/sheet1.xml": "<worksheet xmlns=\"urn:not-spreadsheetml\"/>",
        }

        with self.assertRaisesRegex(OpenXMLValidationError, "XML root"):
            self.validate_parts(parts, ".xlsx")

    def test_accepts_minimal_docx_using_default_content_types(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/></Relationships>'
            ),
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body/></w:document>"
            ),
        }

        self.validate_parts(parts, ".docx")

    def test_accepts_strict_xlsx_core_parts(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument" '
                'Target="xl/workbook.xml"/></Relationships>'
            ),
            "xl/workbook.xml": '<workbook xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main"/>',
            "xl/_rels/workbook.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://purl.oclc.org/ooxml/officeDocument/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/></Relationships>'
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main"><sheetData/></worksheet>'
            ),
        }

        self.validate_parts(parts, ".xlsx")

    def test_accepts_minimal_pptx_core_relationships(self) -> None:
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="ppt/presentation.xml"/></Relationships>'
            ),
            "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
            "ppt/_rels/presentation.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
                'Target="slides/slide1.xml"/></Relationships>'
            ),
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld/></p:sld>'
            ),
        }

        self.validate_parts(parts, ".pptx")


if __name__ == "__main__":
    unittest.main()

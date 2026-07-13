# noqa: SIZE_OK - One bounded parser leaf owns the coupled DOCX, PPTX, and XLSX indexing algorithms.
"""Bounded content extraction for Office Open XML document indexes."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from office_openxml import relationship_target, relationship_types
from office_openxml_index import IndexPackageLimitError, IndexPackageLimits
from office_openxml_index import open_index_package


class DocumentIndexError(RuntimeError):
    """Raised when a bounded document index package cannot be opened."""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_text(element: ET.Element) -> str:
    values = [
        node.text or ""
        for node in element.iter()
        if local_name(node.tag) in {"t", "instrText", "delText"}
    ]
    return "".join(values).strip()


def natural_part_key(name: str) -> tuple[Any, ...]:
    return tuple(
        int(piece) if piece.isdigit() else piece.lower()
        for piece in re.split(r"(\d+)", name)
    )


def chunk(
    ordinal: int,
    locator: str,
    heading: str,
    text: str,
) -> dict[str, Any]:
    normalized = re.sub(r"[ \t]+\n", "\n", text).strip()
    return {
        "ordinal": ordinal,
        "locator": locator,
        "heading": heading.strip(),
        "text": normalized,
        "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def split_text_chunks(
    text: str,
    locator_prefix: str,
    heading: str,
    start_ordinal: int,
    max_chars: int = 12000,
) -> list[dict[str, Any]]:
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    results: list[dict[str, Any]] = []
    current: list[str] = []
    current_length = 0
    ordinal = start_ordinal
    part = 1
    for paragraph in paragraphs or [text.strip()]:
        while len(paragraph) > max_chars:
            if current:
                results.append(
                    chunk(
                        ordinal,
                        f"{locator_prefix};part={part}",
                        heading,
                        "\n\n".join(current),
                    )
                )
                ordinal += 1
                part += 1
                current = []
                current_length = 0
            results.append(
                chunk(
                    ordinal,
                    f"{locator_prefix};part={part}",
                    heading,
                    paragraph[:max_chars],
                )
            )
            ordinal += 1
            part += 1
            paragraph = paragraph[max_chars:]
        if current and current_length + 2 + len(paragraph) > max_chars:
            results.append(
                chunk(
                    ordinal,
                    f"{locator_prefix};part={part}",
                    heading,
                    "\n\n".join(current),
                )
            )
            ordinal += 1
            part += 1
            current = []
            current_length = 0
        if paragraph:
            current_length += (2 if current else 0) + len(paragraph)
            current.append(paragraph)
    if current:
        results.append(
            chunk(
                ordinal,
                f"{locator_prefix};part={part}",
                heading,
                "\n\n".join(current),
            )
        )
    return results


def extract_docx(path: Path, limits: IndexPackageLimits) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with open_index_package(path, limits) as package:
            root = ET.fromstring(package.read("word/document.xml"))
            body = next((item for item in root.iter() if local_name(item.tag) == "body"), root)
            sections: list[tuple[str, list[str]]] = []
            current_heading = "Document"
            current_items: list[str] = []
            for child in list(body):
                name = local_name(child.tag)
                if name == "p":
                    text = element_text(child)
                    if not text:
                        continue
                    style = ""
                    for node in child.iter():
                        if local_name(node.tag) == "pStyle":
                            style = next(
                                (
                                    value
                                    for key, value in node.attrib.items()
                                    if local_name(key) == "val"
                                ),
                                "",
                            )
                            break
                    if re.search(r"heading|title|標題", style, re.IGNORECASE):
                        if current_items:
                            sections.append((current_heading, current_items))
                        current_heading = text
                        current_items = [text]
                    else:
                        current_items.append(text)
                elif name == "tbl":
                    rows: list[str] = []
                    for row in child.iter():
                        if local_name(row.tag) != "tr":
                            continue
                        cells = [
                            element_text(cell)
                            for cell in list(row)
                            if local_name(cell.tag) == "tc"
                        ]
                        if cells:
                            rows.append("\t".join(cells))
                    if rows:
                        current_items.append("\n".join(rows))
            if current_items:
                sections.append((current_heading, current_items))
            ordinal = 0
            for heading, items in sections:
                section_chunks = split_text_chunks(
                    "\n\n".join(items),
                    f"heading={heading}",
                    heading,
                    ordinal,
                )
                results.extend(section_chunks)
                ordinal += len(section_chunks)
            extra_parts = sorted(
                [
                    name
                    for name in package.namelist()
                    if re.match(
                        r"word/(?:header|footer|footnotes|endnotes|comments)\d*\.xml$",
                        name,
                        re.IGNORECASE,
                    )
                ],
                key=natural_part_key,
            )
            for part in extra_parts:
                text = element_text(ET.fromstring(package.read(part)))
                if text:
                    results.append(
                        chunk(ordinal, f"part={part}", PurePosixPath(part).stem, text)
                    )
                    ordinal += 1
    except IndexPackageLimitError as error:
        raise DocumentIndexError(str(error)) from error
    return results


def extract_pptx(path: Path, limits: IndexPackageLimits) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with open_index_package(path, limits) as package:
            names = set(package.namelist())
            presentation_part = "ppt/presentation.xml"
            has_presentation_part = presentation_part in names
            if has_presentation_part:
                presentation_root = ET.fromstring(package.read(presentation_part))
                slide_relationships = relationship_map(
                    package,
                    presentation_part,
                    relationship_types("slide"),
                )
                slide_parts: list[str] = []
                seen_slide_parts: set[str] = set()
                for node in presentation_root.iter():
                    if local_name(node.tag) != "sldId":
                        continue
                    relation_id = next(
                        (
                            value
                            for key, value in node.attrib.items()
                            if key.startswith("{") and local_name(key) == "id"
                        ),
                        "",
                    )
                    part = slide_relationships.get(relation_id, "")
                    if part and part in names and part not in seen_slide_parts:
                        slide_parts.append(part)
                        seen_slide_parts.add(part)
            else:
                slide_parts = sorted(
                    [
                        name
                        for name in names
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                    ],
                    key=natural_part_key,
                )
            for ordinal, part in enumerate(slide_parts):
                number_match = re.search(r"(\d+)", PurePosixPath(part).stem)
                slide_number = int(number_match.group(1)) if number_match else ordinal + 1
                slide_root = ET.fromstring(package.read(part))
                slide_texts = [
                    (node.text or "").strip()
                    for node in slide_root.iter()
                    if local_name(node.tag) == "t" and (node.text or "").strip()
                ]
                notes_texts: list[str] = []
                notes_part = next(
                    (
                        target
                        for target in relationship_map(
                            package,
                            part,
                            relationship_types("notesSlide"),
                        ).values()
                        if target in names
                    ),
                    (
                        ""
                        if has_presentation_part
                        else f"ppt/notesSlides/notesSlide{slide_number}.xml"
                    ),
                )
                if notes_part in names:
                    notes_root = ET.fromstring(package.read(notes_part))
                    notes_texts = [
                        (node.text or "").strip()
                        for node in notes_root.iter()
                        if local_name(node.tag) == "t" and (node.text or "").strip()
                    ]
                heading = slide_texts[0] if slide_texts else f"Slide {slide_number}"
                combined = "\n".join(slide_texts)
                if notes_texts:
                    combined += "\n\nNotes:\n" + "\n".join(notes_texts)
                if combined.strip():
                    results.append(
                        chunk(
                            ordinal,
                            f"slide={slide_number};shape-tree=all",
                            heading,
                            combined,
                        )
                    )
    except IndexPackageLimitError as error:
        raise DocumentIndexError(str(error)) from error
    return results


def read_shared_strings(package: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in package.namelist():
        return []
    root = ET.fromstring(package.read("xl/sharedStrings.xml"))
    return [
        "".join(
            node.text or ""
            for node in item.iter()
            if local_name(node.tag) == "t"
        )
        for item in root
        if local_name(item.tag) == "si"
    ]


def relationship_map(
    package: zipfile.ZipFile,
    part: str,
    expected_types: frozenset[str] | None = None,
) -> dict[str, str]:
    pure = PurePosixPath(part)
    relationship_part = str(pure.parent / "_rels" / f"{pure.name}.rels")
    if relationship_part not in package.namelist():
        return {}
    root = ET.fromstring(package.read(relationship_part))
    relationships: dict[str, str] = {}
    for node in root:
        if local_name(node.tag) != "Relationship":
            continue
        if expected_types is not None and node.attrib.get("Type") not in expected_types:
            continue
        relation_id = node.attrib.get("Id", "")
        target = node.attrib.get("Target", "")
        if relation_id and target:
            relationships[relation_id] = relationship_target(relationship_part, target)
    return relationships


def extract_xlsx(path: Path, limits: IndexPackageLimits) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with open_index_package(path, limits) as package:
            shared = read_shared_strings(package)
            workbook_part = "xl/workbook.xml"
            root = ET.fromstring(package.read(workbook_part))
            relationships = relationship_map(package, workbook_part)
            sheets: list[tuple[str, str]] = []
            for node in root.iter():
                if local_name(node.tag) != "sheet":
                    continue
                name = node.attrib.get("name", "Sheet")
                relation_id = next(
                    (
                        value
                        for key, value in node.attrib.items()
                        if local_name(key) == "id"
                    ),
                    "",
                )
                target = relationships.get(relation_id, "")
                if target:
                    sheets.append((name, target))
            ordinal = 0
            for sheet_name, part in sheets:
                if part not in package.namelist():
                    continue
                sheet_root = ET.fromstring(package.read(part))
                entries: list[tuple[str, str]] = []
                for cell in sheet_root.iter():
                    if local_name(cell.tag) != "c":
                        continue
                    reference = cell.attrib.get("r", "")
                    cell_type = cell.attrib.get("t", "")
                    formula = ""
                    raw_value = ""
                    inline_value = ""
                    for child in cell:
                        child_name = local_name(child.tag)
                        if child_name == "f":
                            formula = child.text or ""
                        elif child_name == "v":
                            raw_value = child.text or ""
                        elif child_name == "is":
                            inline_value = element_text(child)
                    value = inline_value or raw_value
                    if cell_type == "s" and raw_value:
                        try:
                            value = shared[int(raw_value)]
                        except (ValueError, IndexError):
                            value = raw_value
                    elif cell_type == "b" and raw_value:
                        value = "TRUE" if raw_value == "1" else "FALSE"
                    if formula:
                        rendered = f"{reference}\t={formula}"
                        if value:
                            rendered += f"\t→ {value}"
                    else:
                        rendered = f"{reference}\t{value}"
                    if value or formula:
                        entries.append((reference, rendered))
                for offset in range(0, len(entries), 300):
                    group = entries[offset : offset + 300]
                    first = group[0][0] if group else ""
                    last = group[-1][0] if group else ""
                    text = "\n".join(item[1] for item in group)
                    if text:
                        results.append(
                            chunk(
                                ordinal,
                                f"sheet={sheet_name};range={first}:{last};kind=cells",
                                sheet_name,
                                text,
                            )
                        )
                        ordinal += 1
            table_parts = sorted(
                [
                    name
                    for name in package.namelist()
                    if re.fullmatch(r"xl/tables/table\d+\.xml", name)
                ],
                key=natural_part_key,
            )
            for part in table_parts:
                table_root = ET.fromstring(package.read(part))
                table_name = table_root.attrib.get("displayName", PurePosixPath(part).stem)
                table_range = table_root.attrib.get("ref", "")
                columns = [
                    node.attrib.get("name", "")
                    for node in table_root.iter()
                    if local_name(node.tag) == "tableColumn"
                ]
                results.append(
                    chunk(
                        ordinal,
                        f"table={table_name};range={table_range}",
                        table_name,
                        "\t".join(columns),
                    )
                )
                ordinal += 1
    except IndexPackageLimitError as error:
        raise DocumentIndexError(str(error)) from error
    return results

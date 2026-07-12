#!/usr/bin/env python3
# noqa: SIZE_OK - Task 13 preserves this user-owned single state/publish core; new lifecycle logic is delegated.
"""Local state, indexing, and publishing core for Office OS.

This module deliberately does not author Office files. Codex's installed Office
artifact capabilities create candidates; this core fingerprints and indexes
sources, records bounded workflow state, validates candidates, and publishes
stable derived outputs.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any, Iterable, Iterator, Sequence
import uuid
import xml.etree.ElementTree as ET
import zipfile

from office_candidates import CandidateCleanupResult, CandidateLifecycleError
from office_candidates import MAX_CANDIDATE_AGE_SECONDS
from office_candidates import prune_managed_candidates
from office_candidates import remove_managed_candidate


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


configure_stdio()


READ_WRITE_EXTENSIONS = {".xlsx", ".docx", ".pptx"}
READ_ONLY_EXTENSIONS = {".pdf"}
LEGACY_EXTENSIONS = {".xls", ".doc", ".ppt"}
MACRO_EXTENSIONS = {".xlsm", ".docm", ".pptm"}
KNOWN_EXTENSIONS = (
    READ_WRITE_EXTENSIONS
    | READ_ONLY_EXTENSIONS
    | LEGACY_EXTENSIONS
    | MACRO_EXTENSIONS
)
OUTPUT_DIRECTORY_NAME = "Office OS Output"
BACKUP_COUNT = 3
MAX_PUBLISH_RECORDS = 256
MAX_FULL_TEXT_ROOTS = 32
MAX_PACKAGE_MEMBERS = 10_000
MAX_PACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
LOCK_STALE_SECONDS = 6 * 60 * 60
TEMP_PREFIX = ".office-os-"
FULL_TEXT_ROOTS_SETTING = "full_text_roots"
SENSITIVE_PATTERN = re.compile(
    r"confidential|restricted|secret|internal[ -]?only|機密|密件|限閱|內部限定",
    re.IGNORECASE,
)


class OfficeOSError(RuntimeError):
    """A user-actionable Office OS core error."""


@dataclass(frozen=True, slots=True)
class Fingerprint:
    path: str
    size: int
    mtime_ns: int
    sha256: str
    device: int | None
    inode: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "device": self.device,
            "inode": self.inode,
        }


@dataclass(frozen=True, slots=True)
class FullTextRootUpdate:
    grants: tuple[str, ...]
    revocations: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def canonical_path(path: str | Path) -> Path:
    return Path(os.path.realpath(os.path.abspath(os.fspath(path))))


def canonical_workspace(cwd: str | Path | None = None) -> str:
    return os.path.normcase(os.fspath(canonical_path(cwd or os.getcwd())))


def plugin_data_root() -> Path:
    configured = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if configured:
        return canonical_path(configured)
    return canonical_path(Path(tempfile.gettempdir()) / "office-os-plugin-data")


def cleanup_managed_candidates(
    *, older_than_seconds: int = MAX_CANDIDATE_AGE_SECONDS
) -> CandidateCleanupResult:
    try:
        return prune_managed_candidates(
            plugin_data_root(), older_than_seconds=older_than_seconds
        )
    except CandidateLifecycleError as error:
        raise OfficeOSError(str(error)) from error


def cleanup_run_candidate(state: dict[str, Any]) -> bool:
    value = state.get("candidate")
    if not isinstance(value, str):
        return False
    try:
        return remove_managed_candidate(plugin_data_root(), Path(value))
    except CandidateLifecycleError as error:
        raise OfficeOSError(str(error)) from error


def get_workspace_dir(cwd: str | Path | None = None) -> Path:
    canonical = canonical_workspace(cwd)
    workspace_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    directory = plugin_data_root() / "workspaces" / workspace_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{TEMP_PREFIX}{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def state_lock(directory: Path, timeout: float = 1.0) -> Iterator[None]:
    lock_path = directory / "run-state.lock"
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                lock_is_stale = time.time() - lock_path.stat().st_mtime > 120
            except OSError:
                lock_is_stale = False
            if lock_is_stale:
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise OfficeOSError("Office OS state is busy; retry the current chunk.")
            time.sleep(0.025)
    try:
        os.write(descriptor, f"{os.getpid()} {time.time()}".encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def fingerprint(path: str | Path) -> Fingerprint:
    source = canonical_path(path)
    if not source.is_file():
        raise OfficeOSError(f"File not found: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    stat = source.stat()
    return Fingerprint(
        path=os.path.normcase(os.fspath(source)),
        size=stat.st_size,
        mtime_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
        sha256=digest.hexdigest(),
        device=getattr(stat, "st_dev", None),
        inode=getattr(stat, "st_ino", None),
    )


def fingerprint_sources(paths: Iterable[str | Path]) -> list[Fingerprint]:
    values = [fingerprint(path) for path in paths]
    return sorted(values, key=lambda item: item.path)


def combined_source_digest(values: Sequence[Fingerprint]) -> str | None:
    if not values:
        return None
    digest = hashlib.sha256()
    for item in values:
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.sha256.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def stable_task_key(task: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "-", task.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if normalized:
        return normalized[:80]
    return hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]


def safe_filename_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "Office OS Result")[:100]


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


def detect_sensitivity(path: Path) -> tuple[str, str]:
    extension = path.suffix.lower()
    if extension in MACRO_EXTENSIONS:
        return "metadata-only", "macro-enabled extension"
    if SENSITIVE_PATTERN.search(os.fspath(path)):
        return "metadata-only", "protected path or filename"
    if extension in READ_WRITE_EXTENSIONS:
        try:
            with zipfile.ZipFile(path) as package:
                names = [name.lower() for name in package.namelist()]
                if any("vbaproject.bin" in name for name in names):
                    return "metadata-only", "VBA project part"
                if any(
                    name.startswith("_xmlsignatures/")
                    or "origin.sigs" in name
                    or name.endswith(".sigs")
                    for name in names
                ):
                    return "metadata-only", "digital signature part"
                property_parts = [
                    name
                    for name in package.namelist()
                    if name.lower().startswith("docprops/")
                    and name.lower().endswith(".xml")
                ]
                for part in property_parts:
                    raw = package.read(part).decode("utf-8", errors="ignore")
                    if SENSITIVE_PATTERN.search(raw):
                        return "metadata-only", "sensitivity property"
        except (OSError, zipfile.BadZipFile):
            return "metadata-only", "encrypted or unreadable Open XML package"
    if extension == ".pdf":
        try:
            from pypdf import PdfReader
            from pypdf.errors import PyPdfError

            reader = PdfReader(path)
            if reader.is_encrypted:
                return "metadata-only", "encrypted PDF"
        except ImportError:
            return "normal", ""
        except (OSError, ValueError, PyPdfError):
            return "metadata-only", "unreadable PDF"
    return "normal", ""


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
        if current and current_length + len(paragraph) > max_chars:
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
            current.append(paragraph)
            current_length += len(paragraph)
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


def extract_docx(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as package:
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
    return results


def extract_pptx(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
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
            notes_part = f"ppt/notesSlides/notesSlide{slide_number}.xml"
            notes_texts: list[str] = []
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


def relationship_map(package: zipfile.ZipFile, part: str) -> dict[str, str]:
    pure = PurePosixPath(part)
    relationship_part = str(pure.parent / "_rels" / f"{pure.name}.rels")
    if relationship_part not in package.namelist():
        return {}
    root = ET.fromstring(package.read(relationship_part))
    relationships: dict[str, str] = {}
    for node in root:
        if local_name(node.tag) != "Relationship":
            continue
        relation_id = node.attrib.get("Id", "")
        target = node.attrib.get("Target", "")
        if target.startswith("/"):
            normalized = target.lstrip("/")
        else:
            normalized = str(PurePosixPath(pure.parent, target))
        relationships[relation_id] = str(PurePosixPath(normalized))
    return relationships


def extract_xlsx(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as package:
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
    return results


def extract_pdf(path: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise OfficeOSError(
            "PDF text extraction requires the bundled pypdf library; metadata was still indexed."
        ) from error
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise OfficeOSError("Encrypted PDF content is metadata-only.")
    results: list[dict[str, Any]] = []
    page_buffer: list[str] = []
    page_start = 1
    ordinal = 0
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            page_buffer.append(f"[Page {index}]\n{text}")
        if len("\n\n".join(page_buffer)) >= 12000 or index == len(reader.pages):
            if page_buffer:
                results.append(
                    chunk(
                        ordinal,
                        f"pages={page_start}-{index}",
                        f"Pages {page_start}-{index}",
                        "\n\n".join(page_buffer),
                    )
                )
                ordinal += 1
            page_buffer = []
            page_start = index + 1
    return results


def extract_chunks(path: Path) -> list[dict[str, Any]]:
    extension = path.suffix.lower()
    if extension == ".docx":
        return extract_docx(path)
    if extension == ".pptx":
        return extract_pptx(path)
    if extension == ".xlsx":
        return extract_xlsx(path)
    if extension == ".pdf":
        return extract_pdf(path)
    raise OfficeOSError(f"Content extraction requires conversion for {extension}.")


def object_type(extension: str) -> str:
    return {
        ".xlsx": "excel",
        ".xls": "excel",
        ".xlsm": "excel",
        ".docx": "word",
        ".doc": "word",
        ".docm": "word",
        ".pptx": "powerpoint",
        ".ppt": "powerpoint",
        ".pptm": "powerpoint",
        ".pdf": "pdf",
    }[extension]


def connect_database(directory: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(directory / "office.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            extension TEXT NOT NULL,
            object_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            sensitivity_reason TEXT NOT NULL,
            index_policy TEXT NOT NULL,
            index_status TEXT NOT NULL,
            title TEXT NOT NULL,
            modified_at TEXT,
            indexed_at TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            locator TEXT NOT NULL,
            heading TEXT NOT NULL,
            text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            UNIQUE(document_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY,
            source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            source_locator TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            target_path_or_key TEXT NOT NULL,
            target_locator TEXT NOT NULL,
            confidence REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                document_id UNINDEXED,
                locator,
                heading,
                text,
                tokenize='trigram'
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('fts_tokenizer', 'trigram')"
        )
    except sqlite3.OperationalError as error:
        if "tokenizer" not in str(error).lower():
            raise
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                document_id UNINDEXED,
                locator,
                heading,
                text,
                tokenize='unicode61'
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('fts_tokenizer', 'unicode61-fallback')"
        )
    connection.commit()
    return connection


def ignored_path(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        OUTPUT_DIRECTORY_NAME.lower() in lowered_parts
        or name.startswith("~$")
        or name.startswith(TEMP_PREFIX)
        or re.search(r"\.bak\.[123]$", name) is not None
    )


def discover_paths(inputs: Sequence[str]) -> tuple[list[Path], list[Path]]:
    files: set[Path] = set()
    roots: set[Path] = set()
    for raw in inputs:
        path = canonical_path(raw)
        if path.is_file():
            if path.suffix.lower() in KNOWN_EXTENSIONS and not ignored_path(path):
                files.add(path)
        elif path.is_dir():
            roots.add(path)
            for candidate in path.rglob("*"):
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() in KNOWN_EXTENSIONS
                    and not ignored_path(candidate)
                ):
                    files.add(canonical_path(candidate))
        else:
            raise OfficeOSError(f"Index path not found: {path}")
    return sorted(files, key=lambda item: os.fspath(item).lower()), sorted(roots)


def path_is_under(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath([path, root])
        return os.path.normcase(common) == os.path.normcase(os.fspath(root))
    except ValueError:
        return False


def load_full_text_roots(
    connection: sqlite3.Connection, workspace_root: Path
) -> set[Path]:
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?", (FULL_TEXT_ROOTS_SETTING,)
    ).fetchone()
    if row is None:
        return set()
    try:
        stored = json.loads(row[0])
    except json.JSONDecodeError as exc:
        raise OfficeOSError("Stored full-text root policy is invalid.") from exc
    if not isinstance(stored, list) or not all(
        isinstance(item, str) for item in stored
    ):
        raise OfficeOSError("Stored full-text root policy is invalid.")
    roots = {canonical_path(item) for item in stored}
    if any(not path_is_under(root, workspace_root) for root in roots):
        raise OfficeOSError("Stored full-text root is outside the configured workspace root.")
    return roots


def update_full_text_roots(
    connection: sqlite3.Connection,
    workspace_root: Path,
    update: FullTextRootUpdate,
) -> set[Path]:
    roots = load_full_text_roots(connection, workspace_root)
    for raw in update.revocations:
        root = canonical_path(raw)
        if not path_is_under(root, workspace_root):
            raise OfficeOSError(
                f"Full-text root is outside the configured workspace root: {root}"
            )
        roots.discard(root)
    for raw in update.grants:
        root = canonical_path(raw)
        if not root.is_dir():
            raise OfficeOSError(f"Full-text root is not a directory: {root}")
        if not path_is_under(root, workspace_root):
            raise OfficeOSError(
                f"Full-text root is outside the configured workspace root: {root}"
            )
        roots.add(root)
    if len(roots) > MAX_FULL_TEXT_ROOTS:
        raise OfficeOSError(
            f"At most {MAX_FULL_TEXT_ROOTS} persistent full-text roots are allowed per workspace."
        )
    serialized = json.dumps(
        sorted(os.fspath(root) for root in roots), ensure_ascii=False
    )
    with connection:
        connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
            (FULL_TEXT_ROOTS_SETTING, serialized),
        )
    return roots


def replace_document(
    connection: sqlite3.Connection,
    path: Path,
    file_fingerprint: Fingerprint,
    sensitivity: str,
    sensitivity_reason: str,
    policy: str,
    status: str,
    chunks: Sequence[dict[str, Any]],
    error: str = "",
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO documents(
                path, extension, object_type, size, mtime_ns, sha256,
                sensitivity, sensitivity_reason, index_policy, index_status,
                title, modified_at, indexed_at, error
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                extension=excluded.extension,
                object_type=excluded.object_type,
                size=excluded.size,
                mtime_ns=excluded.mtime_ns,
                sha256=excluded.sha256,
                sensitivity=excluded.sensitivity,
                sensitivity_reason=excluded.sensitivity_reason,
                index_policy=excluded.index_policy,
                index_status=excluded.index_status,
                title=excluded.title,
                modified_at=excluded.modified_at,
                indexed_at=excluded.indexed_at,
                error=excluded.error
            """,
            (
                file_fingerprint.path,
                path.suffix.lower(),
                object_type(path.suffix.lower()),
                file_fingerprint.size,
                file_fingerprint.mtime_ns,
                file_fingerprint.sha256,
                sensitivity,
                sensitivity_reason,
                policy,
                status,
                path.stem,
                datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                utc_now(),
                error[:500],
            ),
        )
        document_id = int(
            connection.execute(
                "SELECT id FROM documents WHERE path = ?", (file_fingerprint.path,)
            ).fetchone()[0]
        )
        connection.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
        connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        for item in chunks:
            cursor = connection.execute(
                """
                INSERT INTO chunks(
                    document_id, ordinal, locator, heading, text, content_hash
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    item["ordinal"],
                    item["locator"],
                    item["heading"],
                    item["text"],
                    item["content_hash"],
                ),
            )
            connection.execute(
                """
                INSERT INTO chunk_fts(rowid, document_id, locator, heading, text)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    document_id,
                    item["locator"],
                    item["heading"],
                    item["text"],
                ),
            )


def purge_deleted(
    connection: sqlite3.Connection,
    indexed_files: set[str],
    roots: Sequence[Path],
) -> int:
    rows = connection.execute("SELECT id, path FROM documents").fetchall()
    purge_ids: list[int] = []
    for row in rows:
        stored = canonical_path(row["path"])
        in_scope = any(path_is_under(stored, root) for root in roots)
        if in_scope and os.path.normcase(os.fspath(stored)) not in indexed_files:
            purge_ids.append(int(row["id"]))
    with connection:
        for document_id in purge_ids:
            connection.execute(
                "DELETE FROM chunk_fts WHERE document_id = ?", (document_id,)
            )
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return len(purge_ids)


def command_index(args: argparse.Namespace) -> int:
    workspace_root = canonical_path(args.cwd or os.getcwd())
    requested_paths = [
        canonical_path(item)
        for item in (args.path or [os.fspath(workspace_root)])
    ]
    for requested in requested_paths:
        if not path_is_under(requested, workspace_root):
            raise OfficeOSError(
                f"Index path is outside the configured workspace root: {requested}"
            )
    directory = get_workspace_dir(args.cwd)
    files, roots = discover_paths([os.fspath(path) for path in requested_paths])
    escaped = next(
        (path for path in files if not path_is_under(path, workspace_root)),
        None,
    )
    if escaped is not None:
        raise OfficeOSError(
            f"Discovered index file resolves outside the configured workspace root: {escaped}"
        )
    allow_sensitive = {
        os.path.normcase(os.fspath(canonical_path(item)))
        for item in (args.allow_sensitive_content or [])
    }
    connection = connect_database(directory)
    full_text_roots = update_full_text_roots(
        connection,
        workspace_root,
        FullTextRootUpdate(
            grants=tuple(args.grant_full_text_root or []),
            revocations=tuple(args.revoke_full_text_root or []),
        ),
    )
    stats = {
        "discovered": len(files),
        "indexed": 0,
        "metadata_only": 0,
        "unchanged": 0,
        "errors": 0,
        "purged": 0,
    }
    indexed_paths: set[str] = set()
    try:
        for path in files:
            file_fingerprint = fingerprint(path)
            indexed_paths.add(file_fingerprint.path)
            sensitivity, reason = detect_sensitivity(path)
            explicit_allow = file_fingerprint.path in allow_sensitive
            root_allows_full_text = any(
                path_is_under(path, root) for root in full_text_roots
            )
            policy = (
                "full-text"
                if not args.metadata_only
                and (
                    (sensitivity == "normal" and root_allows_full_text)
                    or explicit_allow
                )
                else "metadata-only"
            )
            existing = connection.execute(
                """
                SELECT sha256, index_policy, index_status
                FROM documents WHERE path = ?
                """,
                (file_fingerprint.path,),
            ).fetchone()
            if (
                existing
                and existing["sha256"] == file_fingerprint.sha256
                and existing["index_policy"] == policy
                and existing["index_status"] == "complete"
            ):
                stats["unchanged"] += 1
                continue
            extracted: list[dict[str, Any]] = []
            status = "complete"
            error = ""
            if policy == "full-text" and path.suffix.lower() not in (
                LEGACY_EXTENSIONS | MACRO_EXTENSIONS
            ):
                try:
                    extracted = extract_chunks(path)
                except (OfficeOSError, OSError, zipfile.BadZipFile, ET.ParseError) as exc:
                    status = "error"
                    error = str(exc)
                    stats["errors"] += 1
            else:
                stats["metadata_only"] += 1
            replace_document(
                connection,
                path,
                file_fingerprint,
                sensitivity,
                reason,
                policy,
                status,
                extracted,
                error,
            )
            stats["indexed"] += 1
        stats["purged"] = purge_deleted(connection, indexed_paths, roots)
        tokenizer_row = connection.execute(
            "SELECT value FROM settings WHERE key = 'fts_tokenizer'"
        ).fetchone()
        stats["fts_tokenizer"] = tokenizer_row[0] if tokenizer_row else "unknown"
        stats["full_text_roots"] = len(full_text_roots)
        stats["database"] = os.fspath(directory / "office.db")
        json_print(stats)
    finally:
        connection.close()
    return 0


def fts_phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def filter_current_query_rows(
    connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> list[sqlite3.Row]:
    current: list[sqlite3.Row] = []
    stale_document_ids: set[int] = set()
    for row in rows:
        try:
            current_sha256 = fingerprint(row["path"]).sha256
        except OfficeOSError:
            current_sha256 = ""
        if current_sha256 == row["document_sha256"]:
            current.append(row)
        else:
            stale_document_ids.add(int(row["document_id"]))
    if stale_document_ids:
        with connection:
            for document_id in stale_document_ids:
                connection.execute(
                    "DELETE FROM chunk_fts WHERE document_id = ?", (document_id,)
                )
                connection.execute(
                    "DELETE FROM chunks WHERE document_id = ?", (document_id,)
                )
                connection.execute(
                    "UPDATE documents SET index_status = 'stale' WHERE id = ?",
                    (document_id,),
                )
    return current


def command_query(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    connection = connect_database(directory)
    filters: list[str] = []
    parameters: list[Any] = []
    if args.object:
        filters.append("d.object_type = ?")
        parameters.append(args.object)
    if args.path_prefix:
        filters.append("d.path LIKE ?")
        parameters.append(os.fspath(canonical_path(args.path_prefix)) + "%")
    filter_sql = " AND " + " AND ".join(filters) if filters else ""
    rows: list[sqlite3.Row] = []
    try:
        if len(args.text.strip()) >= 3:
            rows = connection.execute(
                f"""
                SELECT d.id AS document_id, d.path,
                       d.sha256 AS document_sha256, d.object_type,
                       c.locator, c.heading, c.text, c.content_hash,
                       bm25(chunk_fts) AS rank
                FROM chunk_fts
                JOIN chunks c ON c.id = chunk_fts.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE chunk_fts MATCH ? {filter_sql}
                ORDER BY rank
                LIMIT ?
                """,
                [fts_phrase(args.text.strip()), *parameters, args.limit],
            ).fetchall()
            rows = filter_current_query_rows(connection, rows)
        if not rows:
            like = f"%{args.text.strip()}%"
            rows = connection.execute(
                f"""
                SELECT d.id AS document_id, d.path,
                       d.sha256 AS document_sha256, d.object_type,
                       c.locator, c.heading, c.text, c.content_hash,
                       0.0 AS rank
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE (c.text LIKE ? OR c.heading LIKE ? OR d.title LIKE ?)
                {filter_sql}
                LIMIT ?
                """,
                [like, like, like, *parameters, args.limit],
            ).fetchall()
            rows = filter_current_query_rows(connection, rows)
        json_print(
            {
                "query": args.text,
                "count": len(rows),
                "results": [
                    {
                        "path": row["path"],
                        "object": row["object_type"],
                        "locator": row["locator"],
                        "heading": row["heading"],
                        "text": row["text"][: args.max_chars],
                        "content_hash": row["content_hash"],
                        "rank": row["rank"],
                    }
                    for row in rows
                ],
            }
        )
    finally:
        connection.close()
    return 0


def single_flight_path(directory: Path) -> Path:
    return directory / "single-flight.lock"


def acquire_single_flight(directory: Path, run_id: str, task_key: str) -> bool:
    path = single_flight_path(directory)
    if path.exists():
        try:
            if time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS:
                path.unlink(missing_ok=True)
            else:
                return False
        except OSError:
            return False
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        payload = json.dumps(
            {
                "run_id": run_id,
                "task_key": task_key,
                "created_at": utc_now(),
            }
        ).encode("utf-8")
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    return True


def release_single_flight(directory: Path, run_id: str | None) -> None:
    path = single_flight_path(directory)
    if not path.exists():
        return
    data = read_json(path, {})
    if not run_id or not isinstance(data, dict) or data.get("run_id") == run_id:
        path.unlink(missing_ok=True)


def run_state_path(directory: Path) -> Path:
    return directory / "run_state.json"


def read_run_state(directory: Path) -> dict[str, Any]:
    value = read_json(run_state_path(directory), {})
    return value if isinstance(value, dict) else {}


def command_begin(args: argparse.Namespace) -> int:
    candidate_cleanup = cleanup_managed_candidates()
    directory = get_workspace_dir(args.cwd)
    if args.units < 0:
        raise OfficeOSError("Unit count cannot be negative.")
    source_fingerprints = fingerprint_sources(args.source or [])
    task_key = stable_task_key(args.task)
    run_id = uuid.uuid4().hex
    with state_lock(directory):
        existing = read_run_state(directory)
        if existing.get("status") in {
            "grounding",
            "agreed",
            "executing",
            "validating",
            "publishing",
            "awaiting_user",
        }:
            json_print(
                {
                    "status": "overlap_skipped",
                    "task_key": task_key,
                    "active_run_id": existing.get("run_id"),
                    "reason": "another Office OS run is active in this workspace",
                }
            )
            return 0
        if args.mode == "scheduled" and not acquire_single_flight(
            directory, run_id, task_key
        ):
            json_print(
                {
                    "status": "overlap_skipped",
                    "task_key": task_key,
                    "reason": "another scheduled Office OS run is active",
                }
            )
            return 0
        state = {
            "run_id": run_id,
            "task_key": task_key,
            "task": args.task,
            "intent": args.intent,
            "object": args.object,
            "permission": args.permission,
            "qa": args.qa,
            "mode": args.mode,
            "sources": [item.as_dict() for item in source_fingerprints],
            "source_digest": combined_source_digest(source_fingerprints),
            "status": "executing",
            "total_units": args.units,
            "remaining_units": args.units,
            "progress_marker": "",
            "last_stop_marker": "",
            "continuation_count": 0,
            "no_progress_stops": 0,
            "waiting_for_user": False,
            "candidate_cleanup": {
                "removed_count": candidate_cleanup["removed_count"],
                "remaining_files": candidate_cleanup["remaining_files"],
                "remaining_bytes": candidate_cleanup["remaining_bytes"],
            },
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_json(run_state_path(directory), state)
    json_print(state)
    return 0


def command_progress(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory):
        state = read_run_state(directory)
        if not state:
            raise OfficeOSError("No active Office OS run.")
        if args.remaining < 0:
            raise OfficeOSError("Remaining units cannot be negative.")
        state["remaining_units"] = args.remaining
        state["progress_marker"] = args.marker
        state["status"] = args.status
        state["waiting_for_user"] = False
        state["updated_at"] = utc_now()
        write_json(run_state_path(directory), state)
    json_print(state)
    return 0


def command_await_user(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory):
        state = read_run_state(directory)
        if not state:
            raise OfficeOSError("No active Office OS run.")
        state["status"] = "awaiting_user"
        state["waiting_for_user"] = True
        state["updated_at"] = utc_now()
        write_json(run_state_path(directory), state)
    json_print(state)
    return 0


def command_complete(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory):
        state = read_run_state(directory)
        summary = {
            "run_id": state.get("run_id") if state else None,
            "task_key": state.get("task_key") if state else None,
            "status": "complete",
            "summary": args.summary,
            "completed_at": utc_now(),
            "candidate_removed": cleanup_run_candidate(state) if state else False,
        }
        write_json(directory / "latest_summary.json", summary)
        run_state_path(directory).unlink(missing_ok=True)
        release_single_flight(directory, state.get("run_id") if state else None)
    json_print(summary)
    return 0


def command_fail(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory):
        state = read_run_state(directory)
        if not state:
            raise OfficeOSError("No active Office OS run.")
        state["status"] = "failed"
        state["waiting_for_user"] = False
        state["error"] = args.reason[:500]
        state["updated_at"] = utc_now()
        state["candidate_removed"] = cleanup_run_candidate(state)
        write_json(run_state_path(directory), state)
        release_single_flight(directory, state.get("run_id"))
    json_print(state)
    return 0


def command_status(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    json_print(
        {
            "workspace": os.fspath(directory),
            "active": read_run_state(directory) or None,
            "latest": read_json(directory / "latest_summary.json", None),
        }
    )
    return 0


def validate_candidate(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OfficeOSError(f"Candidate not found: {path}")
    extension = path.suffix.lower()
    if extension not in READ_WRITE_EXTENSIONS:
        raise OfficeOSError(
            f"Writable candidates must be .xlsx, .docx, or .pptx; got {extension or 'no extension'}."
        )
    if path.stat().st_size <= 0:
        raise OfficeOSError("Candidate is empty.")
    required_parts = {
        ".xlsx": {
            "[Content_Types].xml",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
        },
        ".docx": {"[Content_Types].xml", "word/document.xml"},
        ".pptx": {"[Content_Types].xml", "ppt/presentation.xml"},
    }
    try:
        with zipfile.ZipFile(path) as package:
            members = package.infolist()
            if len(members) > MAX_PACKAGE_MEMBERS:
                raise OfficeOSError("Candidate package contains too many parts.")
            if sum(member.file_size for member in members) > MAX_PACKAGE_UNCOMPRESSED_BYTES:
                raise OfficeOSError("Candidate package expands beyond the validation limit.")
            bad_member = package.testzip()
            if bad_member:
                raise OfficeOSError(f"Candidate package has a corrupt part: {bad_member}")
            names = set(package.namelist())
            missing = sorted(required_parts[extension] - names)
            if missing:
                raise OfficeOSError(
                    f"Candidate package is missing {', '.join(missing)}."
                )
            if extension == ".xlsx" and not any(
                name.startswith("xl/worksheets/") and name.endswith(".xml")
                for name in names
            ):
                raise OfficeOSError("Candidate workbook has no worksheet part.")
            if extension == ".pptx" and not any(
                name.startswith("ppt/slides/slide") and name.endswith(".xml")
                for name in names
            ):
                raise OfficeOSError("Candidate presentation has no slide part.")
    except zipfile.BadZipFile as error:
        raise OfficeOSError("Candidate is not a valid Open XML package.") from error
    return {
        "path": os.fspath(path),
        "extension": extension,
        "size": path.stat().st_size,
        "sha256": fingerprint(path).sha256,
    }


def output_target(
    source: Path | None,
    candidate: Path,
    task: str,
    requested_target: str | None,
    cwd: str | None,
) -> tuple[Path, Path]:
    parent = source.parent if source else canonical_path(cwd or os.getcwd())
    output_directory = parent / OUTPUT_DIRECTORY_NAME
    output_directory.mkdir(parents=True, exist_ok=True)
    output_directory = canonical_path(output_directory)
    if requested_target:
        requested = canonical_path(requested_target)
        if requested.parent != output_directory:
            raise OfficeOSError(
                f"Output target must be directly under {output_directory}."
            )
        target = requested
    else:
        task_name = safe_filename_part(task)
        if source:
            filename = f"{safe_filename_part(source.stem)} - {task_name}{candidate.suffix.lower()}"
        else:
            filename = f"{task_name}{candidate.suffix.lower()}"
        target = output_directory / filename
    if target.suffix.lower() != candidate.suffix.lower():
        raise OfficeOSError("Output target and candidate extensions must match.")
    return output_directory, target


def backup_path(target: Path, number: int) -> Path:
    return target.with_name(f"{target.name}.bak.{number}")


def rotate_backups(target: Path) -> None:
    oldest = backup_path(target, BACKUP_COUNT)
    oldest.unlink(missing_ok=True)
    for number in range(BACKUP_COUNT - 1, 0, -1):
        current = backup_path(target, number)
        following = backup_path(target, number + 1)
        if current.exists():
            os.replace(current, following)


def windows_replace_file(target: Path, replacement: Path, backup: Path) -> None:
    replace_file = ctypes.windll.kernel32.ReplaceFileW
    replace_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace_file.restype = ctypes.c_int
    success = replace_file(
        os.fspath(target),
        os.fspath(replacement),
        os.fspath(backup),
        0,
        None,
        None,
    )
    if not success:
        raise ctypes.WinError()


def scheduled_replace(target: Path, stage: Path) -> str:
    rotate_backups(target)
    first_backup = backup_path(target, 1)
    if os.name == "nt":
        try:
            windows_replace_file(target, stage, first_backup)
            return "ReplaceFileW"
        except OSError:
            shutil.copy2(target, first_backup)
            os.replace(stage, target)
            return "copy-plus-os.replace fallback"
    shutil.copy2(target, first_backup)
    os.replace(stage, target)
    return "copy-plus-os.replace"


def publish_state_path(directory: Path) -> Path:
    return directory / "publish_state.json"


def read_publish_records(directory: Path) -> dict[str, Any]:
    value = read_json(publish_state_path(directory), {"tasks": {}})
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), dict):
        return {"tasks": {}}
    return value


def update_publish_record(
    directory: Path,
    task_key: str,
    target: Path,
    source_fingerprints: Sequence[Fingerprint],
    output_fingerprint: Fingerprint,
) -> None:
    records = read_publish_records(directory)
    tasks: dict[str, Any] = records["tasks"]
    tasks[task_key] = {
        "target": os.fspath(target),
        "source_digest": combined_source_digest(source_fingerprints),
        "sources": [item.as_dict() for item in source_fingerprints],
        "output_sha256": output_fingerprint.sha256,
        "published_at": utc_now(),
    }
    live_items = [
        (key, value)
        for key, value in tasks.items()
        if isinstance(value, dict)
        and value.get("target")
        and Path(value["target"]).exists()
    ]
    live_items.sort(
        key=lambda item: str(item[1].get("published_at") or ""), reverse=True
    )
    records["tasks"] = dict(live_items[:MAX_PUBLISH_RECORDS])
    write_json(publish_state_path(directory), records)


def source_unchanged(
    directory: Path,
    task_key: str,
    target: Path,
    current: Sequence[Fingerprint],
) -> bool:
    if not current or not target.exists():
        return False
    record = read_publish_records(directory)["tasks"].get(task_key, {})
    return (
        isinstance(record, dict)
        and record.get("target") == os.fspath(target)
        and record.get("source_digest") == combined_source_digest(current)
    )


def require_publish_authority(
    directory: Path, task_key: str, mode: str
) -> dict[str, Any]:
    state = read_run_state(directory)
    if not state or state.get("task_key") != task_key:
        raise OfficeOSError(
            "Publishing requires a matching active Office OS run and explicit write authority."
        )
    if state.get("status") not in {"executing", "validating", "publishing"}:
        raise OfficeOSError("The active Office OS run is not ready to publish.")
    required_permission = (
        "scheduled-overwrite" if mode == "scheduled" else "fixed-output-write"
    )
    if state.get("permission") != required_permission:
        raise OfficeOSError(
            f"Active permission {state.get('permission') or 'none'} does not authorize publishing in {mode} mode."
        )
    if state.get("mode") != mode:
        raise OfficeOSError("Publish mode does not match the active Office OS run.")
    if mode == "scheduled":
        lease = read_json(single_flight_path(directory), {})
        if (
            not isinstance(lease, dict)
            or lease.get("run_id") != state.get("run_id")
            or lease.get("task_key") != task_key
        ):
            raise OfficeOSError(
                "Scheduled publishing requires its matching active single-flight lease."
            )
    return state


def prepare_stage(candidate: Path, output_directory: Path) -> tuple[Path, bool]:
    if candidate.parent == output_directory and candidate.name.startswith(TEMP_PREFIX):
        return candidate, False
    stage = output_directory / f"{TEMP_PREFIX}{uuid.uuid4().hex}{candidate.suffix.lower()}"
    shutil.copy2(candidate, stage)
    return stage, True


def command_needs_run(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    sources = [canonical_path(item) for item in args.source]
    source_fingerprints = fingerprint_sources(sources)
    primary_source = sources[0]
    task_key = stable_task_key(args.task)
    candidate_suffix = args.extension.lower()
    if candidate_suffix not in READ_WRITE_EXTENSIONS:
        raise OfficeOSError("Output extension must be .xlsx, .docx, or .pptx.")
    placeholder = primary_source.with_suffix(candidate_suffix)
    _, target = output_target(
        primary_source, placeholder, args.task, args.target, args.cwd
    )
    unchanged = source_unchanged(
        directory, task_key, target, source_fingerprints
    )
    json_print(
        {
            "task_key": task_key,
            "sources": [item.as_dict() for item in source_fingerprints],
            "source_digest": combined_source_digest(source_fingerprints),
            "target": os.fspath(target),
            "needs_run": not unchanged,
            "reason": "source_changed_or_no_output" if not unchanged else "unchanged",
        }
    )
    return 0


def publish_candidate(args: argparse.Namespace, directory: Path) -> int:
    candidate_input = Path(os.path.abspath(os.fspath(args.candidate)))
    candidate = canonical_path(candidate_input)
    sources = [canonical_path(item) for item in (args.source or [])]
    primary_source = sources[0] if sources else None
    sources_before = fingerprint_sources(sources)
    task_key = stable_task_key(args.task)
    active_run = require_publish_authority(directory, task_key, args.mode)
    active_run["candidate"] = os.fspath(candidate_input)
    active_run["updated_at"] = utc_now()
    write_json(run_state_path(directory), active_run)
    if (
        active_run.get("task_key") == task_key
        and active_run.get("source_digest") is not None
        and active_run.get("source_digest")
        != combined_source_digest(sources_before)
    ):
        raise OfficeOSError(
            "A source differs from the task-start fingerprint; candidate was not published."
        )
    output_directory, target = output_target(
        primary_source, candidate, args.task, args.target, args.cwd
    )
    if args.mode == "scheduled" and source_unchanged(
        directory, task_key, target, sources_before
    ):
        if candidate.parent == output_directory and candidate.name.startswith(TEMP_PREFIX):
            candidate.unlink(missing_ok=True)
        candidate_removed = cleanup_run_candidate(active_run)
        json_print(
            {
                "status": "unchanged",
                "task_key": task_key,
                "target": os.fspath(target),
                "backups_created": 0,
                "candidate_removed": candidate_removed,
            }
        )
        return 0
    validation = validate_candidate(candidate)
    stage, copied = prepare_stage(candidate, output_directory)
    try:
        stage_validation = validate_candidate(stage)
        sources_after = fingerprint_sources(sources)
        if combined_source_digest(sources_after) != combined_source_digest(sources_before):
            raise OfficeOSError(
                "A source changed during the run; candidate was not published."
            )
        if target.exists():
            method = (
                scheduled_replace(target, stage)
                if args.mode == "scheduled"
                else "os.replace"
            )
            if args.mode == "manual":
                os.replace(stage, target)
        else:
            os.replace(stage, target)
            method = "os.replace-new"
        output_fingerprint = fingerprint(target)
        update_publish_record(
            directory, task_key, target, sources_before, output_fingerprint
        )
        candidate_removed = cleanup_run_candidate(active_run)
        json_print(
            {
                "status": "published",
                "task_key": task_key,
                "target": os.fspath(target),
                "method": method,
                "validation": stage_validation,
                "sources_unchanged": (
                    combined_source_digest(fingerprint_sources(sources))
                    == combined_source_digest(sources_before)
                    if sources
                    else None
                ),
                "candidate_removed": candidate_removed,
            }
        )
    finally:
        if stage.exists() and stage != target:
            stage.unlink(missing_ok=True)
        if copied and candidate.parent == output_directory and candidate.name.startswith(
            TEMP_PREFIX
        ):
            candidate.unlink(missing_ok=True)
    return 0


def command_publish(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory, timeout=5.0):
        return publish_candidate(args, directory)


def command_cleanup(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    removed: list[str] = []
    roots = [canonical_path(args.path)] if args.path else [canonical_path(args.cwd or os.getcwd())]
    cutoff = time.time() - args.older_than_seconds
    managed = cleanup_managed_candidates(older_than_seconds=args.older_than_seconds)
    removed.extend(str(path) for path in managed["removed"])
    for root in roots:
        output_directory = root / OUTPUT_DIRECTORY_NAME
        if output_directory.is_dir():
            for candidate in output_directory.glob(f"{TEMP_PREFIX}*"):
                try:
                    if candidate.is_file() and candidate.stat().st_mtime <= cutoff:
                        candidate.unlink()
                        removed.append(os.fspath(candidate))
                except OSError:
                    continue
    for temporary in directory.glob(f"{TEMP_PREFIX}*"):
        try:
            if temporary.stat().st_mtime <= cutoff:
                if temporary.is_dir():
                    shutil.rmtree(temporary)
                else:
                    temporary.unlink()
                removed.append(os.fspath(temporary))
        except OSError:
            continue
    lock = single_flight_path(directory)
    if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
        lock.unlink(missing_ok=True)
        removed.append(os.fspath(lock))
    removed_count = len(removed) - len(managed["removed"]) + int(
        managed["removed_count"]
    )
    json_print(
        {
            "removed_count": removed_count,
            "removed": removed,
            "managed_candidates": managed,
        }
    )
    return 0


def fts_diagnostic() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE test_fts USING fts5(text, tokenize='trigram')"
        )
        connection.execute(
            "INSERT INTO test_fts(text) VALUES(?)", ("台北辦公室季度報告",)
        )
        count = connection.execute(
            "SELECT count(*) FROM test_fts WHERE test_fts MATCH ?", ('"辦公室"',)
        ).fetchone()[0]
        return {"available": True, "chinese_match_count": count}
    except sqlite3.OperationalError as error:
        return {"available": False, "error": str(error)}
    finally:
        connection.close()


def command_doctor(args: argparse.Namespace) -> int:
    workspace = get_workspace_dir(args.cwd)
    diagnostic = {
        "status": "ok",
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "fts5_trigram": fts_diagnostic(),
        "plugin_data": os.fspath(plugin_data_root()),
        "workspace": os.fspath(workspace),
        "windows_replace_file": bool(os.name == "nt" and hasattr(ctypes, "windll")),
        "supported": {
            "read_write": sorted(READ_WRITE_EXTENSIONS),
            "read_only": sorted(READ_ONLY_EXTENSIONS),
            "conversion_required": sorted(LEGACY_EXTENSIONS | MACRO_EXTENSIONS),
        },
    }
    if not diagnostic["fts5_trigram"]["available"]:
        diagnostic["status"] = "degraded"
    json_print(diagnostic)
    return 0


def command_fingerprint(args: argparse.Namespace) -> int:
    json_print(fingerprint(args.path).as_dict())
    return 0


def add_common_cwd(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cwd",
        default=os.getcwd(),
        help="Workspace root; defaults to the current directory.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Office OS local indexing, state, and stable publishing core."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local runtime support.")
    add_common_cwd(doctor)
    doctor.set_defaults(function=command_doctor)

    fingerprint_parser = subparsers.add_parser(
        "fingerprint", help="Return a source fingerprint."
    )
    fingerprint_parser.add_argument("path")
    fingerprint_parser.set_defaults(function=command_fingerprint)

    index = subparsers.add_parser("index", help="Upsert the local Office map.")
    add_common_cwd(index)
    index.add_argument("--path", action="append", help="File or folder; repeatable.")
    index.add_argument("--metadata-only", action="store_true")
    index.add_argument(
        "--grant-full-text-root",
        action="append",
        help="Persist explicit full-text consent for a directory inside this workspace.",
    )
    index.add_argument(
        "--revoke-full-text-root",
        action="append",
        help="Remove persistent full-text consent for a directory.",
    )
    index.add_argument(
        "--allow-sensitive-content",
        action="append",
        help="Explicit sensitive file path allowed for this indexing operation.",
    )
    index.set_defaults(function=command_index)

    query = subparsers.add_parser("query", help="Query the local Office map.")
    add_common_cwd(query)
    query.add_argument("--text", required=True)
    query.add_argument(
        "--object", choices=["excel", "word", "powerpoint", "pdf"]
    )
    query.add_argument("--path-prefix")
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--max-chars", type=int, default=3000)
    query.set_defaults(function=command_query)

    begin = subparsers.add_parser("begin", help="Start one bounded Office run.")
    add_common_cwd(begin)
    begin.add_argument("--task", required=True)
    begin.add_argument("--intent", required=True)
    begin.add_argument("--object", required=True)
    begin.add_argument(
        "--permission",
        required=True,
        choices=["read-only", "fixed-output-write", "scheduled-overwrite"],
    )
    begin.add_argument("--qa", required=True)
    begin.add_argument("--units", type=int, required=True)
    begin.add_argument(
        "--source",
        action="append",
        help="Source file fingerprinted at task start; repeat for cross-file tasks.",
    )
    begin.add_argument("--mode", choices=["manual", "scheduled"], default="manual")
    begin.set_defaults(function=command_begin)

    progress = subparsers.add_parser("progress", help="Record real chunk progress.")
    add_common_cwd(progress)
    progress.add_argument("--remaining", type=int, required=True)
    progress.add_argument("--marker", required=True)
    progress.add_argument(
        "--status",
        choices=["executing", "validating", "publishing"],
        default="executing",
    )
    progress.set_defaults(function=command_progress)

    await_user = subparsers.add_parser(
        "await-user", help="Pause for an owner decision."
    )
    add_common_cwd(await_user)
    await_user.set_defaults(function=command_await_user)

    complete = subparsers.add_parser("complete", help="Close and prune a run.")
    add_common_cwd(complete)
    complete.add_argument("--summary", required=True)
    complete.set_defaults(function=command_complete)

    fail = subparsers.add_parser("fail", help="Close a failed scheduled lease.")
    add_common_cwd(fail)
    fail.add_argument("--reason", required=True)
    fail.set_defaults(function=command_fail)

    status = subparsers.add_parser("status", help="Show bounded workspace state.")
    add_common_cwd(status)
    status.set_defaults(function=command_status)

    needs_run = subparsers.add_parser(
        "needs-run", help="Check scheduled fingerprint no-op."
    )
    add_common_cwd(needs_run)
    needs_run.add_argument("--source", action="append", required=True)
    needs_run.add_argument("--task", required=True)
    needs_run.add_argument("--extension", required=True)
    needs_run.add_argument("--target")
    needs_run.set_defaults(function=command_needs_run)

    publish = subparsers.add_parser(
        "publish", help="Validate and replace one stable derived output."
    )
    add_common_cwd(publish)
    publish.add_argument("--candidate", required=True)
    publish.add_argument(
        "--source",
        action="append",
        help="Source file; repeat for cross-file tasks.",
    )
    publish.add_argument("--task", required=True)
    publish.add_argument("--target")
    publish.add_argument(
        "--mode", choices=["manual", "scheduled"], default="manual"
    )
    publish.set_defaults(function=command_publish)

    cleanup = subparsers.add_parser(
        "cleanup", help="Remove only known stale Office OS temporary artifacts."
    )
    add_common_cwd(cleanup)
    cleanup.add_argument("--path")
    cleanup.add_argument("--older-than-seconds", type=int, default=3600)
    cleanup.set_defaults(function=command_cleanup)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.function(args))
    except OfficeOSError as error:
        json_print({"status": "error", "error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

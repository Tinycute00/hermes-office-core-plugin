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
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import time
from typing import Any, Iterable, Iterator, Sequence
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile

from office_candidates import CandidateCleanupResult, CandidateLifecycleError
from office_candidates import MAX_CANDIDATE_AGE_SECONDS
from office_candidates import is_linklike
from office_candidates import prune_managed_candidates
from office_candidates import remove_managed_candidate
from office_candidate_runs import active_run_candidate_paths
from office_candidate_runs import remove_candidate_directory
from office_candidate_runs import reserve_candidate_directory
from office_candidate_runs import validated_run_candidate
from office_document_index import DocumentIndexError
from office_document_index import chunk as _chunk
from office_document_index import extract_docx as _extract_docx
from office_document_index import extract_pptx as _extract_pptx
from office_document_index import extract_xlsx as _extract_xlsx
from office_openxml_index import IndexPackageLimitError, IndexPackageLimits
from office_openxml_index import open_index_package
from office_openxml import OpenXMLValidationError, validate_openxml
from office_state import StateLeafError, ensure_private_state_leaf
from office_state import open_private_state_leaf, unlink_private_state_leaf
from office_state import validate_private_state_leaf


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
MAX_WORKSPACE_STATES = 256
MAX_FULL_TEXT_ROOTS = 32
MAX_KNOWLEDGE_DOCUMENTS = 256
MAX_KNOWLEDGE_CHUNKS = 4_096
MAX_KNOWLEDGE_TEXT_BYTES = 16 * 1024 * 1024
MAX_INDEX_PACKAGE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_INDEX_PACKAGE_MEMBERS = 10_000
MAX_INDEX_PACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_INDEX_PACKAGE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_INDEX_PACKAGE_COMPRESSION_RATIO = 100
MAX_QUERY_RESULTS = 100
MAX_QUERY_TEXT_CHARS = 8_000
MAX_RUN_UNITS = 10_000
MAX_PROGRESS_MARKER_BYTES = 512
LOCK_STALE_SECONDS = 6 * 60 * 60
TEMP_PREFIX = ".office-os-"
FULL_TEXT_ROOTS_SETTING = "full_text_roots"
SENSITIVE_PATTERN = re.compile(
    r"confidential|restricted|secret|internal[ -]?only|機密|密件|限閱|內部限定",
    re.IGNORECASE,
)
ACTIVE_RUN_STATUSES = {
    "grounding",
    "agreed",
    "awaiting_confirmation",
    "executing",
    "validating",
    "publishing",
    "awaiting_user",
}


class OfficeOSError(RuntimeError):
    """A user-actionable Office OS core error."""


def validate_state_leaf(path: Path, description: str) -> os.stat_result | None:
    try:
        return validate_private_state_leaf(path, description)
    except StateLeafError as error:
        raise OfficeOSError(str(error)) from error


def open_state_leaf(
    path: Path,
    flags: int,
    description: str,
    *,
    create: bool = False,
    exclusive: bool = False,
) -> int:
    try:
        return open_private_state_leaf(
            path, flags, description, create=create, exclusive=exclusive
        )
    except StateLeafError as error:
        raise OfficeOSError(str(error)) from error


def ensure_state_leaf(path: Path, description: str) -> None:
    try:
        ensure_private_state_leaf(path, description)
    except StateLeafError as error:
        raise OfficeOSError(str(error)) from error


def unlink_state_leaf(path: Path, description: str, *, missing_ok: bool = False) -> None:
    try:
        unlink_private_state_leaf(path, description, missing_ok=missing_ok)
    except StateLeafError as error:
        raise OfficeOSError(str(error)) from error


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


def long_windows_path(value: str) -> str:
    if os.name != "nt":
        return value
    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetLongPathNameW(value, buffer, len(buffer))
    if not length or length >= len(buffer):
        return value
    return buffer.value


def canonical_path(path: str | Path) -> Path:
    resolved = os.path.realpath(os.path.abspath(os.fspath(path)))
    return Path(long_windows_path(resolved))


def canonical_workspace(cwd: str | Path | None = None) -> str:
    return os.path.normcase(os.fspath(canonical_path(cwd or os.getcwd())))


def plugin_data_root() -> Path:
    configured = os.environ.get("PLUGIN_DATA")
    if not configured:
        raise OfficeOSError("Office OS requires the hook-injected PLUGIN_DATA value.")
    return Path(os.path.abspath(configured))


def validate_plugin_data_ancestors(root: Path) -> None:
    for component in (*reversed(root.parents), root):
        if not os.path.lexists(component):
            continue
        if is_linklike(component) or not component.is_dir():
            raise OfficeOSError(
                "Managed OfficeCLI plugin data root is linked or invalid."
            )


def ensure_plugin_data_root() -> Path:
    root = plugin_data_root()
    validate_plugin_data_ancestors(root)
    if os.path.lexists(root):
        if is_linklike(root) or not root.is_dir():
            raise OfficeOSError("Managed OfficeCLI plugin data root is linked or invalid.")
    else:
        root.mkdir(parents=True)
    validate_plugin_data_ancestors(root)
    if is_linklike(root) or not root.is_dir():
        raise OfficeOSError("Managed OfficeCLI plugin data root is linked or invalid.")
    return root


def active_candidate_paths(data_root: Path) -> list[Path]:
    validate_plugin_data_ancestors(data_root)
    workspaces = data_root / "workspaces"
    if not os.path.lexists(workspaces):
        return []
    if is_linklike(workspaces) or not workspaces.is_dir():
        raise OfficeOSError("Office OS workspace state root is linked or invalid.")
    active: list[Path] = []
    for workspace in workspaces.iterdir():
        if is_linklike(workspace) or not workspace.is_dir():
            raise OfficeOSError("Office OS workspace state entry is linked or invalid.")
        path = workspace / "run_state.json"
        if not os.path.lexists(path):
            continue
        if is_linklike(path) or not path.is_file():
            raise OfficeOSError("Office OS run state is linked or invalid.")
        state = read_json_strict(path, "Office OS run state")
        if not isinstance(state, dict):
            raise OfficeOSError("Office OS run state must be a JSON object.")
        if state.get("status") not in ACTIVE_RUN_STATUSES:
            continue
        try:
            active.extend(active_run_candidate_paths(data_root, state))
        except CandidateLifecycleError as error:
            raise OfficeOSError(f"Invalid active Office OS run state: {error}") from error
    return active


def cleanup_managed_candidates(
    *, older_than_seconds: int = MAX_CANDIDATE_AGE_SECONDS
) -> CandidateCleanupResult:
    data_root = plugin_data_root()
    try:
        if not os.path.lexists(data_root):
            return prune_managed_candidates(
                data_root, older_than_seconds=older_than_seconds
            )
        if is_linklike(data_root) or not data_root.is_dir():
            raise CandidateLifecycleError(
                "Managed OfficeCLI plugin data root is linked or invalid."
            )
        with state_lock(data_root):
            return prune_managed_candidates(
                data_root,
                older_than_seconds=older_than_seconds,
                preserve=active_candidate_paths(data_root),
            )
    except CandidateLifecycleError as error:
        raise OfficeOSError(str(error)) from error


def cleanup_run_candidate(state: dict[str, Any]) -> bool:
    data_root = plugin_data_root()
    try:
        if is_linklike(data_root) or not data_root.is_dir():
            raise CandidateLifecycleError(
                "Managed OfficeCLI plugin data root is linked or invalid."
            )
        with state_lock(data_root):
            removed = False
            candidate = state.get("candidate")
            if isinstance(candidate, str):
                removed = remove_managed_candidate(data_root, Path(candidate))
            candidate_directory = state.get("candidate_directory")
            if isinstance(candidate_directory, str):
                removed = (
                    remove_candidate_directory(data_root, candidate_directory) or removed
                )
            return removed
    except CandidateLifecycleError as error:
        raise OfficeOSError(str(error)) from error


def cleanup_after_committed_publish(state: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        return cleanup_run_candidate(state), None
    except (OfficeOSError, OSError) as error:
        return False, str(error)


def workspace_state_is_active(directory: Path) -> bool:
    return read_run_state(directory).get("status") in ACTIVE_RUN_STATUSES


def remove_inactive_workspace_state(directory: Path) -> None:
    pending = [directory]
    directories: list[Path] = []
    while pending:
        current = pending.pop()
        try:
            current_status = current.lstat()
        except OSError as error:
            raise OfficeOSError(
                f"Cannot inspect Office OS workspace state entry: {error}"
            ) from error
        if is_linklike(current) or not stat.S_ISDIR(current_status.st_mode):
            raise OfficeOSError("Office OS workspace state entry is linked or invalid.")
        directories.append(current)
        try:
            children = list(current.iterdir())
        except OSError as error:
            raise OfficeOSError(
                f"Cannot inspect Office OS workspace state entry: {error}"
            ) from error
        for child in children:
            try:
                child_status = child.lstat()
            except OSError as error:
                raise OfficeOSError(
                    f"Cannot inspect Office OS workspace state entry: {error}"
                ) from error
            if is_linklike(child):
                try:
                    if stat.S_ISDIR(child_status.st_mode):
                        child.rmdir()
                    else:
                        child.unlink()
                except OSError as error:
                    raise OfficeOSError(
                        f"Cannot remove Office OS workspace link entry: {error}"
                    ) from error
            elif stat.S_ISDIR(child_status.st_mode):
                pending.append(child)
            elif stat.S_ISREG(child_status.st_mode):
                try:
                    child.unlink()
                except OSError as error:
                    raise OfficeOSError(
                        f"Cannot remove Office OS workspace state entry: {error}"
                    ) from error
            else:
                raise OfficeOSError("Office OS workspace state entry is invalid.")
    for current in reversed(directories):
        try:
            current.rmdir()
        except OSError as error:
            raise OfficeOSError(
                f"Cannot remove Office OS workspace state entry: {error}"
            ) from error


def prune_inactive_workspace_states(
    workspaces: Path, current: Path, limit: int = MAX_WORKSPACE_STATES
) -> int:
    try:
        entries = list(workspaces.iterdir())
    except OSError as error:
        raise OfficeOSError(f"Cannot inspect Office OS workspace state root: {error}") from error
    inactive: list[tuple[int, str, Path]] = []
    for workspace in entries:
        try:
            status = workspace.lstat()
        except OSError as error:
            raise OfficeOSError(
                f"Cannot inspect Office OS workspace state entry: {error}"
            ) from error
        if is_linklike(workspace) or not stat.S_ISDIR(status.st_mode):
            raise OfficeOSError("Office OS workspace state entry is linked or invalid.")
        if workspace != current and not workspace_state_is_active(workspace):
            inactive.append((status.st_mtime_ns, workspace.name, workspace))
    remaining = len(entries)
    for _modified, _name, workspace in sorted(inactive):
        if remaining <= limit:
            break
        remove_inactive_workspace_state(workspace)
        remaining -= 1
    return remaining


def get_workspace_dir(cwd: str | Path | None = None) -> Path:
    canonical = canonical_workspace(cwd)
    workspace_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    root = ensure_plugin_data_root()
    with state_lock(root):
        workspaces = root / "workspaces"
        if os.path.lexists(workspaces):
            if is_linklike(workspaces) or not workspaces.is_dir():
                raise OfficeOSError("Office OS workspace state root is linked or invalid.")
        else:
            workspaces.mkdir()
        directory = workspaces / workspace_id
        exists = os.path.lexists(directory)
        if exists:
            if is_linklike(directory) or not directory.is_dir():
                raise OfficeOSError("Office OS workspace state entry is linked or invalid.")
            prune_inactive_workspace_states(workspaces, directory)
        else:
            remaining = prune_inactive_workspace_states(
                workspaces, directory, MAX_WORKSPACE_STATES - 1
            )
            if remaining >= MAX_WORKSPACE_STATES:
                raise OfficeOSError(
                    "Office OS workspace state limit is full; complete or fail an active "
                    "run before starting work in another workspace."
                )
            directory.mkdir()
        if directory.resolve(strict=True).parent != workspaces.resolve(strict=True):
            raise OfficeOSError("Office OS workspace state entry escapes plugin data.")
        return directory


def read_json(path: Path, default: Any) -> Any:
    try:
        descriptor = open_state_leaf(path, os.O_RDONLY, "Office OS state leaf")
    except FileNotFoundError:
        return default
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_json_strict(path: Path, label: str) -> Any:
    try:
        descriptor = open_state_leaf(path, os.O_RDONLY, label)
    except FileNotFoundError as error:
        raise OfficeOSError(f"{label} is unreadable or malformed.") from error
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OfficeOSError(f"{label} is unreadable or malformed.") from error


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{TEMP_PREFIX}{path.name}.{os.getpid()}.tmp")
    validate_state_leaf(path, "Office OS state leaf")
    descriptor = open_state_leaf(
        temporary,
        os.O_WRONLY,
        "Office OS state temporary",
        create=True,
        exclusive=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        validate_state_leaf(temporary, "Office OS state temporary")
        validate_state_leaf(path, "Office OS state leaf")
        os.replace(temporary, path)
        validate_state_leaf(path, "Office OS state leaf")
    except (OSError, TypeError, ValueError, OfficeOSError):
        if os.path.lexists(temporary):
            unlink_state_leaf(temporary, "Office OS state temporary")
        raise


@contextlib.contextmanager
def state_lock(directory: Path, timeout: float = 1.0) -> Iterator[None]:
    lock_path = directory / "run-state.lock"
    deadline = time.monotonic() + timeout
    descriptor = open_state_leaf(
        lock_path, os.O_RDWR, "Office OS state lock", create=True
    )
    locked = False
    owner_token = uuid.uuid4().hex
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        while not locked:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise OfficeOSError(
                        "Office OS state is busy; retry the current chunk."
                    )
                time.sleep(0.025)
        owner = json.dumps(
            {"pid": os.getpid(), "token": owner_token},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, owner)
        os.ftruncate(descriptor, len(owner))
        os.fsync(descriptor)
        yield
    finally:
        if locked:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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


def collision_safe_part(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[: maximum - len(suffix) - 1]}-{suffix}"


def canonical_task_identity(task: str) -> str:
    normalized = unicodedata.normalize("NFKC", task)
    return re.sub(r"\s+", " ", normalized.strip()).casefold()


def stable_task_key(task: str) -> str:
    identity = canonical_task_identity(task)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    readable = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "-", identity)
    readable = re.sub(r"-+", "-", readable).strip("-") or "task"
    return f"{readable[:67].rstrip('-')}-{digest}"


def safe_filename_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return collision_safe_part(cleaned or "Office OS Result", 100)


def safe_task_filename(task: str) -> str:
    identity = canonical_task_identity(task)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", identity).strip(" .-")
    readable = cleaned or "Office OS Result"
    return f"{readable[:87].rstrip(' .-')}-{digest}"


def bounded_integer(value: str, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{label} must be an integer.") from error
    if parsed < minimum or parsed > maximum:
        raise argparse.ArgumentTypeError(
            f"{label} is out of range ({minimum}..{maximum})."
        )
    return parsed


def query_limit(value: str) -> int:
    return bounded_integer(value, 1, MAX_QUERY_RESULTS, "query limit")


def query_max_chars(value: str) -> int:
    return bounded_integer(value, 1, MAX_QUERY_TEXT_CHARS, "query max-chars")


def _index_package_limits() -> IndexPackageLimits:
    return IndexPackageLimits(
        max_archive_bytes=MAX_INDEX_PACKAGE_ARCHIVE_BYTES,
        max_members=MAX_INDEX_PACKAGE_MEMBERS,
        max_member_bytes=MAX_INDEX_PACKAGE_MEMBER_BYTES,
        max_uncompressed_bytes=MAX_INDEX_PACKAGE_UNCOMPRESSED_BYTES,
        max_compression_ratio=MAX_INDEX_PACKAGE_COMPRESSION_RATIO,
    )


@contextlib.contextmanager
def bounded_index_package(path: Path) -> Iterator[zipfile.ZipFile]:
    """Expose an Open XML package after fixed index resource checks."""

    try:
        with open_index_package(path, _index_package_limits()) as package:
            yield package
    except IndexPackageLimitError as error:
        raise OfficeOSError(str(error)) from error


def detect_sensitivity(path: Path) -> tuple[str, str]:
    extension = path.suffix.lower()
    if extension in READ_ONLY_EXTENSIONS:
        return "metadata-only", "read-only extension"
    if extension in MACRO_EXTENSIONS:
        return "metadata-only", "macro-enabled extension"
    if SENSITIVE_PATTERN.search(os.fspath(path)):
        return "metadata-only", "protected path or filename"
    if extension in READ_WRITE_EXTENSIONS:
        try:
            with bounded_index_package(path) as package:
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
        except OfficeOSError:
            return "metadata-only", "index limit"
        except (OSError, zipfile.BadZipFile):
            return "metadata-only", "encrypted or unreadable Open XML package"
    return "normal", ""


def chunk(
    ordinal: int,
    locator: str,
    heading: str,
    text: str,
) -> dict[str, Any]:
    return _chunk(ordinal, locator, heading, text)


def extract_docx(path: Path) -> list[dict[str, Any]]:
    try:
        return _extract_docx(path, _index_package_limits())
    except DocumentIndexError as error:
        raise OfficeOSError(str(error)) from error


def extract_pptx(path: Path) -> list[dict[str, Any]]:
    try:
        return _extract_pptx(path, _index_package_limits())
    except DocumentIndexError as error:
        raise OfficeOSError(str(error)) from error


def extract_xlsx(path: Path) -> list[dict[str, Any]]:
    try:
        return _extract_xlsx(path, _index_package_limits())
    except DocumentIndexError as error:
        raise OfficeOSError(str(error)) from error


def extract_chunks(path: Path) -> list[dict[str, Any]]:
    extension = path.suffix.lower()
    try:
        if extension == ".docx":
            return extract_docx(path)
        if extension == ".pptx":
            return extract_pptx(path)
        if extension == ".xlsx":
            return extract_xlsx(path)
    except (KeyError, UnicodeError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        raise OfficeOSError(f"Could not extract {extension} content: {error}") from error
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


def database_state_leaves(directory: Path) -> tuple[Path, ...]:
    database = directory / "office.db"
    return (
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        database.with_name(f"{database.name}-journal"),
    )


def validate_database_state_leaves(directory: Path) -> None:
    for path in database_state_leaves(directory):
        validate_state_leaf(path, "Office OS database state")


def connect_database(directory: Path) -> sqlite3.Connection:
    database, *_sidecars = database_state_leaves(directory)
    ensure_state_leaf(database, "Office OS database state")
    validate_database_state_leaves(directory)
    connection = sqlite3.connect(database)
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
    trim_knowledge_map(connection)
    connection.commit()
    validate_database_state_leaves(directory)
    return connection


def close_database(connection: sqlite3.Connection, directory: Path) -> None:
    try:
        validate_database_state_leaves(directory)
    finally:
        connection.close()


def bounded_document_chunks(chunks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    text_bytes = 0
    for item in chunks:
        item_text_bytes = len(item["text"].encode("utf-8"))
        if len(retained) >= MAX_KNOWLEDGE_CHUNKS:
            raise OfficeOSError("Document content exceeds the knowledge-map chunk limit.")
        if text_bytes + item_text_bytes > MAX_KNOWLEDGE_TEXT_BYTES:
            raise OfficeOSError("Document content exceeds the knowledge-map text limit.")
        retained.append(item)
        text_bytes += item_text_bytes
    return retained


def trim_knowledge_map(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT
            d.id,
            COUNT(c.id) AS chunk_count,
            COALESCE(SUM(LENGTH(CAST(c.text AS BLOB))), 0) AS text_bytes
        FROM documents AS d
        LEFT JOIN chunks AS c ON c.document_id = d.id
        GROUP BY d.id
        ORDER BY d.mtime_ns DESC, d.path ASC
        """
    ).fetchall()
    retained_documents = 0
    retained_chunks = 0
    retained_text_bytes = 0
    eviction_ids: list[int] = []
    for row in rows:
        chunk_count = int(row["chunk_count"])
        text_bytes = int(row["text_bytes"])
        over_capacity = (
            retained_documents >= MAX_KNOWLEDGE_DOCUMENTS
            or retained_chunks + chunk_count > MAX_KNOWLEDGE_CHUNKS
            or retained_text_bytes + text_bytes > MAX_KNOWLEDGE_TEXT_BYTES
        )
        if over_capacity:
            eviction_ids.append(int(row["id"]))
            continue
        retained_documents += 1
        retained_chunks += chunk_count
        retained_text_bytes += text_bytes
    for document_id in eviction_ids:
        connection.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return len(eviction_ids)


def ignored_path(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    resolved = canonical_path(path)
    data_root = canonical_path(plugin_data_root())
    return (
        path_is_under(resolved, data_root)
        or OUTPUT_DIRECTORY_NAME.lower() in lowered_parts
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


def validated_cleanup_root(cwd: str | None, requested: str | None) -> Path:
    lexical_workspace = Path(os.path.abspath(cwd or os.getcwd()))
    lexical_root = Path(os.path.abspath(requested or lexical_workspace))
    if not path_is_under(lexical_root, lexical_workspace):
        raise OfficeOSError("Office cleanup root is outside the configured workspace.")
    try:
        relative = lexical_root.relative_to(lexical_workspace)
    except ValueError as error:
        raise OfficeOSError("Office cleanup root is outside the configured workspace.") from error
    components = [lexical_workspace]
    component = lexical_workspace
    for part in relative.parts:
        component /= part
        components.append(component)
    for component in components:
        if os.path.lexists(component) and is_linklike(component):
            raise OfficeOSError("Office cleanup root contains a linked ancestor.")
    workspace_root = canonical_path(lexical_workspace)
    root = canonical_path(lexical_root)
    if not path_is_under(root, workspace_root):
        raise OfficeOSError("Office cleanup root is outside the configured workspace.")
    if not root.is_dir():
        raise OfficeOSError("Office cleanup root is linked or invalid.")
    return root


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
) -> int:
    bounded_chunks = bounded_document_chunks(chunks)
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
        for item in bounded_chunks:
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
        return trim_knowledge_map(connection)


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
        "retention_evicted": 0,
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
                if path.suffix.lower() not in READ_ONLY_EXTENSIONS
                and not args.metadata_only
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
                    extracted = bounded_document_chunks(extract_chunks(path))
                except (OfficeOSError, OSError, zipfile.BadZipFile, ET.ParseError) as exc:
                    status = "error"
                    error = str(exc)
                    stats["errors"] += 1
            else:
                stats["metadata_only"] += 1
            stats["retention_evicted"] += replace_document(
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
        close_database(connection, directory)
    return 0


def fts_phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def filter_current_query_rows(
    connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> list[sqlite3.Row]:
    current: list[sqlite3.Row] = []
    stale_document_ids: set[int] = set()
    current_hashes: dict[int, str] = {}
    for row in rows:
        document_id = int(row["document_id"])
        current_sha256 = current_hashes.get(document_id)
        if current_sha256 is None:
            try:
                current_sha256 = fingerprint(row["path"]).sha256
            except OfficeOSError:
                current_sha256 = ""
            current_hashes[document_id] = current_sha256
        if current_sha256 == row["document_sha256"]:
            current.append(row)
        else:
            stale_document_ids.add(document_id)
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
    limit = args.limit
    max_chars = args.max_chars
    filters: list[str] = []
    parameters: list[Any] = []
    if args.object:
        filters.append("d.object_type = ?")
        parameters.append(args.object)
    if args.path_prefix:
        path_prefix = os.fspath(canonical_path(args.path_prefix))
        escaped_prefix = (
            (path_prefix + os.sep)
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        filters.append("(d.path = ? OR d.path LIKE ? ESCAPE '\\')")
        parameters.extend((path_prefix, escaped_prefix + "%"))
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
                [fts_phrase(args.text.strip()), *parameters, limit],
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
                [like, like, like, *parameters, limit],
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
                        "text": row["text"][:max_chars],
                        "content_hash": row["content_hash"],
                        "rank": row["rank"],
                    }
                    for row in rows
                ],
            }
        )
    finally:
        close_database(connection, directory)
    return 0


def single_flight_path(directory: Path) -> Path:
    return directory / "single-flight.lock"


def acquire_single_flight(directory: Path, run_id: str, task_key: str) -> bool:
    path = single_flight_path(directory)
    status = validate_state_leaf(path, "Office OS scheduled state")
    if status is not None:
        if time.time() - status.st_mtime > LOCK_STALE_SECONDS:
            unlink_state_leaf(path, "Office OS scheduled state")
        else:
            return False
    try:
        descriptor = open_state_leaf(
            path,
            os.O_WRONLY,
            "Office OS scheduled state",
            create=True,
            exclusive=True,
        )
    except OfficeOSError:
        if validate_state_leaf(path, "Office OS scheduled state") is not None:
            return False
        raise
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
    if validate_state_leaf(path, "Office OS scheduled state") is None:
        return
    data = read_json(path, {})
    if not run_id or not isinstance(data, dict) or data.get("run_id") == run_id:
        unlink_state_leaf(path, "Office OS scheduled state", missing_ok=True)


def run_state_path(directory: Path) -> Path:
    return directory / "run_state.json"


def read_run_state(directory: Path) -> dict[str, Any]:
    path = run_state_path(directory)
    if not os.path.lexists(path):
        return {}
    if is_linklike(path) or not path.is_file():
        raise OfficeOSError("Office OS run state is linked or invalid.")
    value = read_json_strict(path, "Office OS run state")
    if not isinstance(value, dict):
        raise OfficeOSError("Office OS run state must be a JSON object.")
    return value


def command_begin(args: argparse.Namespace) -> int:
    if not args.source:
        raise OfficeOSError("Office OS begin requires at least one source.")
    if args.units < 0:
        raise OfficeOSError("Unit count cannot be negative.")
    if args.units > MAX_RUN_UNITS:
        raise OfficeOSError(f"Unit count cannot exceed {MAX_RUN_UNITS}.")
    source_fingerprints = fingerprint_sources(args.source)
    directory = get_workspace_dir(args.cwd)
    data_root = ensure_plugin_data_root()
    task_key = stable_task_key(args.task)
    run_id = uuid.uuid4().hex
    with state_lock(directory):
        existing = read_run_state(directory)
        if existing.get("status") in ACTIVE_RUN_STATUSES:
            json_print(
                {
                    "status": "overlap_skipped",
                    "task_key": task_key,
                    "active_run_id": existing.get("run_id"),
                    "reason": "another Office OS run is active in this workspace",
                }
            )
            return 0
    candidate_cleanup = cleanup_managed_candidates()
    with state_lock(directory):
        existing = read_run_state(directory)
        if existing.get("status") in ACTIVE_RUN_STATUSES:
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
        candidate_directory: Path | None = None
        try:
            with state_lock(data_root):
                candidate_directory = reserve_candidate_directory(data_root, run_id)
                proposal_confirmed = args.permission != "fixed-output-write"
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
                    "status": "executing" if proposal_confirmed else "awaiting_confirmation",
                    "total_units": args.units,
                    "remaining_units": args.units,
                    "progress_marker": "",
                    "last_stop_marker": "",
                    "continuation_count": 0,
                    "no_progress_stops": 0,
                    "waiting_for_user": not proposal_confirmed,
                    "proposal_confirmed": proposal_confirmed,
                    "candidate": None,
                    "candidate_directory": os.fspath(candidate_directory),
                    "candidate_cleanup": {
                        "removed_count": candidate_cleanup["removed_count"],
                        "remaining_files": candidate_cleanup["remaining_files"],
                        "remaining_bytes": candidate_cleanup["remaining_bytes"],
                    },
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
                try:
                    write_json(run_state_path(directory), state)
                except (OSError, OfficeOSError, CandidateLifecycleError):
                    remove_candidate_directory(data_root, candidate_directory)
                    raise
        except (OSError, OfficeOSError, CandidateLifecycleError):
            release_single_flight(directory, run_id)
            raise
    json_print(state)
    return 0


def command_progress(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory):
        state = read_run_state(directory)
        if not state:
            raise OfficeOSError("No active Office OS run.")
        if state.get("proposal_confirmed") is False:
            raise OfficeOSError("Progress requires one explicit proposal confirmation.")
        if args.remaining < 0:
            raise OfficeOSError("Remaining units cannot be negative.")
        total_units = state.get("total_units")
        if not isinstance(total_units, int) or args.remaining > total_units:
            raise OfficeOSError("Remaining units cannot exceed the task total.")
        if len(args.marker.encode("utf-8")) > MAX_PROGRESS_MARKER_BYTES:
            raise OfficeOSError(
                f"Progress marker cannot exceed {MAX_PROGRESS_MARKER_BYTES} bytes."
            )
        state["remaining_units"] = args.remaining
        state["progress_marker"] = args.marker
        state["status"] = args.status
        state["waiting_for_user"] = False
        state["updated_at"] = utc_now()
        write_json(run_state_path(directory), state)
    json_print(state)
    return 0


def command_confirm(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory):
        state = read_run_state(directory)
        if not state:
            raise OfficeOSError("No active Office OS run to confirm.")
        if state.get("permission") != "fixed-output-write":
            raise OfficeOSError("Only a manual fixed-output run needs proposal confirmation.")
        if state.get("status") not in {"awaiting_confirmation", "awaiting_user"}:
            raise OfficeOSError("The active Office OS run is not awaiting confirmation.")
        state["proposal_confirmed"] = True
        state["status"] = "executing"
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
        unlink_state_leaf(
            run_state_path(directory), "Office OS run state", missing_ok=True
        )
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
        with bounded_index_package(path) as package:
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
            try:
                validate_openxml(package, extension)
            except OpenXMLValidationError as error:
                raise OfficeOSError(str(error)) from error
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
    *,
    create_directory: bool = True,
) -> tuple[Path, Path]:
    parent = source.parent if source else canonical_path(cwd or os.getcwd())
    lexical_output = parent / OUTPUT_DIRECTORY_NAME
    if os.path.lexists(lexical_output):
        if is_linklike(lexical_output) or not lexical_output.is_dir():
            raise OfficeOSError("Office OS Output is linked or invalid.")
    elif create_directory:
        lexical_output.mkdir(parents=True)
    if is_linklike(lexical_output):
        raise OfficeOSError("Office OS Output is linked or invalid.")
    output_directory = canonical_path(lexical_output)
    if output_directory.parent != canonical_path(parent):
        raise OfficeOSError("Office OS Output escapes its source workspace.")
    task_name = safe_task_filename(task)
    if source:
        filename = (
            f"{safe_filename_part(source.stem)} - {task_name}"
            f"{candidate.suffix.lower()}"
        )
    else:
        filename = f"{task_name}{candidate.suffix.lower()}"
    target = output_directory / filename
    if requested_target:
        requested = Path(os.path.abspath(requested_target))
        if os.path.normcase(os.fspath(requested)) != os.path.normcase(os.fspath(target)):
            raise OfficeOSError(
                f"Requested output does not match the fixed stable target: {target}"
            )
    if os.path.lexists(target) and is_linklike(target):
        raise OfficeOSError("The stable output target is linked or invalid.")
    if target.suffix.lower() != candidate.suffix.lower():
        raise OfficeOSError("Output target and candidate extensions must match.")
    return output_directory, target


def backup_path(target: Path, number: int) -> Path:
    return target.with_name(f"{target.name}.bak.{number}")


def validate_scheduled_backup_leaves(target: Path) -> None:
    for number in range(1, BACKUP_COUNT + 1):
        validate_state_leaf(
            backup_path(target, number), "Office OS scheduled backup leaf"
        )


def rotate_backups(target: Path) -> None:
    validate_scheduled_backup_leaves(target)
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
    live_items: list[tuple[str, dict[str, Any]]] = []
    for key, value in tasks.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        record_target = value.get("target")
        if not isinstance(record_target, str) or not record_target:
            continue
        try:
            if Path(record_target).exists():
                live_items.append((key, value))
        except (OSError, TypeError, ValueError):
            continue
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
    if state.get("permission") == "fixed-output-write" and state.get("proposal_confirmed") is not True:
        raise OfficeOSError("Publishing requires one explicit proposal confirmation.")
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
    primary_source = Path(source_fingerprints[0].path)
    task_key = stable_task_key(args.task)
    candidate_suffix = args.extension.lower()
    if candidate_suffix not in READ_WRITE_EXTENSIONS:
        raise OfficeOSError("Output extension must be .xlsx, .docx, or .pptx.")
    placeholder = primary_source.with_suffix(candidate_suffix)
    _, target = output_target(
        primary_source,
        placeholder,
        args.task,
        args.target,
        args.cwd,
        create_directory=False,
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
    sources = [canonical_path(item) for item in (args.source or [])]
    task_key = stable_task_key(args.task)
    active_run = require_publish_authority(directory, task_key, args.mode)
    expected_sources = active_run.get("sources")
    if not isinstance(expected_sources, list):
        raise OfficeOSError("Active run source set is invalid.")
    expected_paths: list[str] = []
    for source in expected_sources:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise OfficeOSError("Active run source set is invalid.")
        expected_paths.append(os.path.normcase(os.fspath(canonical_path(source["path"])))
        )
    actual_paths = sorted(os.path.normcase(os.fspath(source)) for source in sources)
    if expected_paths != actual_paths:
        raise OfficeOSError("Publish sources differ from the task-start source set.")
    sources_before = fingerprint_sources(sources)
    if active_run.get("source_digest") != combined_source_digest(sources_before):
        raise OfficeOSError(
            "A source differs from the task-start fingerprint; candidate was not published."
        )
    primary_source = Path(sources_before[0].path) if sources_before else None
    data_root = ensure_plugin_data_root()
    try:
        candidate = validated_run_candidate(data_root, active_run, candidate_input)
    except CandidateLifecycleError as error:
        raise OfficeOSError(str(error)) from error
    active_run["candidate"] = os.fspath(candidate)
    active_run["updated_at"] = utc_now()
    write_json(run_state_path(directory), active_run)
    output_directory, target = output_target(
        primary_source, candidate, args.task, args.target, args.cwd
    )
    if args.mode == "scheduled" and source_unchanged(
        directory, task_key, target, sources_before
    ):
        if candidate.parent == output_directory and candidate.name.startswith(TEMP_PREFIX):
            candidate.unlink(missing_ok=True)
        candidate_removed = cleanup_run_candidate(active_run)
        active_run["candidate"] = None
        active_run["candidate_directory"] = None
        active_run["candidate_cleanup_error"] = None
        active_run["updated_at"] = utc_now()
        write_json(run_state_path(directory), active_run)
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
    validate_candidate(candidate)
    stage, copied = prepare_stage(candidate, output_directory)
    committed = False
    post_commit_errors: list[str] = []
    result: dict[str, Any] | None = None
    try:
        stage_validation = validate_candidate(stage)
        sources_after = fingerprint_sources(sources)
        if combined_source_digest(sources_after) != combined_source_digest(sources_before):
            raise OfficeOSError(
                "A source changed during the run; candidate was not published."
            )
        stage_fingerprint = fingerprint(stage)
        output_fingerprint = Fingerprint(
            path=os.path.normcase(os.fspath(target)),
            size=stage_fingerprint.size,
            mtime_ns=stage_fingerprint.mtime_ns,
            sha256=stage_fingerprint.sha256,
            device=stage_fingerprint.device,
            inode=stage_fingerprint.inode,
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
        committed = True
        try:
            update_publish_record(
                directory, task_key, target, sources_before, output_fingerprint
            )
        except (OSError, OfficeOSError, TypeError, ValueError) as error:
            post_commit_errors.append(f"publish record: {error}")
        candidate_removed, cleanup_error = cleanup_after_committed_publish(active_run)
        if cleanup_error is None:
            active_run["candidate"] = None
            active_run["candidate_directory"] = None
        active_run["candidate_cleanup_error"] = cleanup_error
        active_run["updated_at"] = utc_now()
        try:
            write_json(run_state_path(directory), active_run)
        except (OSError, OfficeOSError, TypeError, ValueError) as error:
            post_commit_errors.append(f"run state: {error}")
        result = {
            "status": "published",
            "task_key": task_key,
            "target": os.fspath(target),
            "method": method,
            "validation": stage_validation,
            "sources_unchanged": True if sources else None,
            "candidate_removed": candidate_removed,
        }
        if cleanup_error:
            result["candidate_cleanup_error"] = cleanup_error
    finally:
        if stage.exists() and stage != target:
            try:
                stage.unlink(missing_ok=True)
            except OSError as error:
                if committed:
                    post_commit_errors.append(f"stage cleanup: {error}")
        if copied and candidate.parent == output_directory and candidate.name.startswith(
            TEMP_PREFIX
        ):
            try:
                candidate.unlink(missing_ok=True)
            except OSError as error:
                if committed:
                    post_commit_errors.append(f"candidate cleanup: {error}")
    if result is None:
        raise OfficeOSError("Publish did not produce a result.")
    if post_commit_errors:
        result["post_commit_errors"] = post_commit_errors
    json_print(result)
    return 0


def command_publish(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    with state_lock(directory, timeout=5.0):
        return publish_candidate(args, directory)


def command_cleanup(args: argparse.Namespace) -> int:
    directory = get_workspace_dir(args.cwd)
    removed: list[str] = []
    roots = [validated_cleanup_root(args.cwd, args.path)]
    cutoff = time.time() - args.older_than_seconds
    managed = cleanup_managed_candidates(older_than_seconds=args.older_than_seconds)
    removed.extend(str(path) for path in managed["removed"])
    for root in roots:
        if is_linklike(root) or not root.is_dir():
            raise OfficeOSError("Office cleanup root is linked or invalid.")
        output_directory = root / OUTPUT_DIRECTORY_NAME
        if os.path.lexists(output_directory):
            if is_linklike(output_directory) or not output_directory.is_dir():
                raise OfficeOSError("Office OS Output is linked or invalid.")
            for candidate in output_directory.glob(f"{TEMP_PREFIX}*"):
                try:
                    if is_linklike(candidate):
                        raise OfficeOSError(
                            "Office OS Output contains a linked temporary entry."
                        )
                    if candidate.is_file() and candidate.stat().st_mtime <= cutoff:
                        candidate.unlink()
                        removed.append(os.fspath(candidate))
                except OfficeOSError:
                    raise
                except OSError:
                    continue
    for temporary in directory.glob(f"{TEMP_PREFIX}*"):
        try:
            if is_linklike(temporary):
                raise OfficeOSError("Office OS state contains a linked temporary entry.")
            if temporary.stat().st_mtime <= cutoff:
                if temporary.is_dir():
                    shutil.rmtree(temporary)
                else:
                    temporary.unlink()
                removed.append(os.fspath(temporary))
        except OfficeOSError:
            raise
        except OSError:
            continue
    lock = single_flight_path(directory)
    lock_status = validate_state_leaf(lock, "Office OS scheduled state")
    if lock_status is not None and time.time() - lock_status.st_mtime > LOCK_STALE_SECONDS:
        unlink_state_leaf(lock, "Office OS scheduled state", missing_ok=True)
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
    query.add_argument("--limit", type=query_limit, default=10)
    query.add_argument("--max-chars", type=query_max_chars, default=3000)
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
        required=True,
        help="Source file fingerprinted at task start; repeat for cross-file tasks.",
    )
    begin.add_argument("--mode", choices=["manual", "scheduled"], default="manual")
    begin.set_defaults(function=command_begin)

    confirm = subparsers.add_parser(
        "confirm", help="Confirm the one manual fixed-output proposal."
    )
    add_common_cwd(confirm)
    confirm.set_defaults(function=command_confirm)

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

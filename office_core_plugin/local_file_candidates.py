from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from time import monotonic
from typing import TYPE_CHECKING, Final, Protocol

from .operation_policy import confidence_band
from .redaction import redact_path_diagnostic, redact_text

if TYPE_CHECKING:
    from os import stat_result
    from pathlib import Path

    from .handler_contract import JSONObject

DEFAULT_MAX_COUNT: Final = 100
DEFAULT_MAX_DEPTH: Final = 5
DEFAULT_MAX_SIZE_BYTES: Final = 100 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: Final = 2.0


class MonotonicClock(Protocol):
    def __call__(self) -> float: ...


@unique
class FileKind(StrEnum):
    EXCEL = "excel"
    WORD = "word"
    PDF = "pdf"
    PPT = "ppt"
    IMAGE = "image"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CandidateDiscoveryConfig:
    allowed_roots: tuple[Path | str, ...]
    observed_at: str
    requested_paths: tuple[Path | str, ...] = ()
    max_depth: int = DEFAULT_MAX_DEPTH
    max_count: int = DEFAULT_MAX_COUNT
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    clock: MonotonicClock = monotonic


@dataclass(frozen=True, slots=True)
class KindHint:
    kind: FileKind
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class LocalFileCandidate:
    path: str
    kind: FileKind
    size: int
    modified_at: str
    confidence: float
    reason: str
    provenance: JSONObject

    def to_dict(self) -> JSONObject:
        return {
            "path": redact_text(self.path),
            "kind": self.kind.value,
            "size": self.size,
            "modified_at": self.modified_at,
            "confidence": self.confidence,
            "confidence_band": confidence_band(self.confidence),
            "reason": self.reason,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class CandidateDenial:
    path: str
    reason: str

    def to_dict(self) -> JSONObject:
        return {"path": redact_path_diagnostic(self.path), "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CandidateAuditEvent:
    event_type: str
    outcome: str
    reason: str
    path: str | None = None

    def to_dict(self) -> JSONObject:
        data: JSONObject = {
            "event_type": self.event_type,
            "outcome": self.outcome,
            "reason": self.reason,
        }
        if self.path is not None:
            data["path"] = redact_path_diagnostic(self.path)
        return data


@dataclass(frozen=True, slots=True)
class CandidateDiscoveryResult:
    success: bool
    candidates: tuple[LocalFileCandidate, ...]
    denials: tuple[CandidateDenial, ...]
    audit: tuple[CandidateAuditEvent, ...]
    timed_out: bool = False

    def to_dict(self) -> JSONObject:
        return {
            "success": self.success,
            "timed_out": self.timed_out,
            "candidates": [item.to_dict() for item in self.candidates],
            "denials": [item.to_dict() for item in self.denials],
            "audit": [item.to_dict() for item in self.audit],
        }


KIND_HINTS: Final[dict[str, KindHint]] = {
    ".xlsx": KindHint(FileKind.EXCEL, 0.88, "spreadsheet extension .xlsx"),
    ".xls": KindHint(FileKind.EXCEL, 0.82, "spreadsheet extension .xls"),
    ".csv": KindHint(FileKind.EXCEL, 0.74, "spreadsheet extension .csv"),
    ".docx": KindHint(FileKind.WORD, 0.86, "word document extension .docx"),
    ".doc": KindHint(FileKind.WORD, 0.78, "word document extension .doc"),
    ".pdf": KindHint(FileKind.PDF, 0.87, "pdf extension .pdf"),
    ".pptx": KindHint(FileKind.PPT, 0.85, "presentation extension .pptx"),
    ".ppt": KindHint(FileKind.PPT, 0.78, "presentation extension .ppt"),
    ".png": KindHint(FileKind.IMAGE, 0.74, "image extension .png"),
    ".jpg": KindHint(FileKind.IMAGE, 0.74, "image extension .jpg"),
    ".jpeg": KindHint(FileKind.IMAGE, 0.74, "image extension .jpeg"),
    ".gif": KindHint(FileKind.IMAGE, 0.72, "image extension .gif"),
    ".webp": KindHint(FileKind.IMAGE, 0.72, "image extension .webp"),
    ".tif": KindHint(FileKind.IMAGE, 0.72, "image extension .tif"),
    ".tiff": KindHint(FileKind.IMAGE, 0.72, "image extension .tiff"),
    ".zip": KindHint(FileKind.ARCHIVE, 0.64, "archive extension .zip"),
    ".7z": KindHint(FileKind.ARCHIVE, 0.62, "archive extension .7z"),
    ".rar": KindHint(FileKind.ARCHIVE, 0.62, "archive extension .rar"),
    ".tar": KindHint(FileKind.ARCHIVE, 0.62, "archive extension .tar"),
    ".gz": KindHint(FileKind.ARCHIVE, 0.60, "archive extension .gz"),
}
UNKNOWN_HINT: Final = KindHint(FileKind.UNKNOWN, 0.34, "unknown extension")


def build_candidate(
    path: Path,
    file_stat: stat_result,
    config: CandidateDiscoveryConfig,
) -> LocalFileCandidate:
    hint = KIND_HINTS.get(path.suffix.casefold(), UNKNOWN_HINT)
    normalized_path = str(path)
    return LocalFileCandidate(
        path=normalized_path,
        kind=hint.kind,
        size=file_stat.st_size,
        modified_at=datetime.fromtimestamp(file_stat.st_mtime, tz=UTC).isoformat(),
        confidence=hint.confidence,
        reason=hint.reason,
        provenance=_provenance(normalized_path, file_stat, hint, config),
    )


def _provenance(
    path: str,
    file_stat: stat_result,
    hint: KindHint,
    config: CandidateDiscoveryConfig,
) -> JSONObject:
    evidence = f"{path}|{file_stat.st_size}|{file_stat.st_mtime}|{hint.kind.value}"
    return {
        "source_type": "local_file_candidate",
        "source_uri": redact_text(f"file://{path}"),
        "observed_at": redact_text(config.observed_at),
        "method": "metadata_scan",
        "evidence_hash": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        "confidence": hint.confidence,
        "confidence_band": confidence_band(hint.confidence),
    }

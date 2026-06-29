from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from office_core_plugin.local_files_adapter import (
    CandidateDiscoveryConfig,
    FileKind,
    discover_local_file_candidates,
)

OBSERVED_AT: Final = "2026-06-29T12:00:00Z"
SYNTHETIC_SECRET: Final = "office_" + "secret_12345"


@dataclass(slots=True)
class StepClock:
    value: float = 0.0
    step: float = 0.0

    def __call__(self) -> float:
        self.value += self.step
        return self.value


@dataclass(frozen=True, slots=True)
class ConfigOptions:
    requested_paths: tuple[Path | str, ...] = ()
    max_depth: int = 4
    max_count: int = 20
    max_size_bytes: int = 10_000
    timeout_seconds: float = 5.0
    clock: StepClock | None = None


def _write(path: Path, content: str = "fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _config(root: Path, options: ConfigOptions | None = None) -> CandidateDiscoveryConfig:
    selected_options = options or ConfigOptions()
    return CandidateDiscoveryConfig(
        allowed_roots=(root,),
        requested_paths=selected_options.requested_paths,
        max_depth=selected_options.max_depth,
        max_count=selected_options.max_count,
        max_size_bytes=selected_options.max_size_bytes,
        timeout_seconds=selected_options.timeout_seconds,
        observed_at=OBSERVED_AT,
        clock=selected_options.clock or StepClock(),
    )


def test_allowlisted_root_returns_candidates_with_metadata_and_provenance(tmp_path: Path) -> None:
    # Given: files below an explicit allowed root.
    root = tmp_path / "allowed"
    report = _write(root / "finance" / "report.xlsx")
    _write(root / "notes.txt")

    # When: the adapter discovers candidates from that root.
    result = discover_local_file_candidates(_config(root))
    payload = result.to_dict()

    # Then: candidates include path, kind, metadata, confidence, reason, and provenance.
    assert payload["success"] is True
    assert payload["denials"] == []
    candidates = payload["candidates"]
    assert len(candidates) == 2
    first = next(item for item in candidates if item["path"].endswith("report.xlsx"))
    assert first["path"] == str(report.resolve())
    assert first["kind"] == FileKind.EXCEL.value
    assert first["size"] == report.stat().st_size
    assert isinstance(first["modified_at"], str)
    assert first["confidence"] >= 0.8
    assert "spreadsheet" in first["reason"]
    assert first["provenance"]["source_type"] == "local_file_candidate"
    assert first["provenance"]["observed_at"] == OBSERVED_AT
    assert first["provenance"]["evidence_hash"]
    assert payload["audit"][0]["event_type"] == "candidate_discovery_started"


def test_traversal_or_absolute_outside_root_returns_denial_without_reading_target(
    tmp_path: Path,
) -> None:
    # Given: an outside target containing synthetic secret text.
    root = tmp_path / "allowed"
    root.mkdir()
    outside = _write(tmp_path / "outside" / "token=office_secret_12345.xlsx", SYNTHETIC_SECRET)
    traversal = Path("..") / outside.parent.name / outside.name

    # When: both traversal and absolute outside paths are requested.
    result = discover_local_file_candidates(
        _config(root, ConfigOptions(requested_paths=(traversal, outside))),
    )
    raw_json = json.dumps(result.to_dict(), sort_keys=True)

    # Then: the adapter denies both requests and never includes target content.
    assert result.success is False
    assert result.candidates == ()
    assert len(result.denials) == 2
    assert {item.reason for item in result.denials} == {"path_outside_allowed_roots"}
    assert "path_denied" in {item.event_type for item in result.audit}
    assert SYNTHETIC_SECRET not in raw_json
    assert "[REDACTED]" in raw_json


def test_symlink_escape_is_denied_without_descending(tmp_path: Path) -> None:
    # Given: an allowed root with a symlink pointing outside it.
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    _write(outside / "escape.pdf")
    link = root / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    # When: the adapter scans the allowed root.
    result = discover_local_file_candidates(_config(root))

    # Then: it records a denial for the escape and excludes the outside file.
    assert result.candidates == ()
    assert [item.reason for item in result.denials] == ["symlink_escape"]
    assert result.audit[-1].event_type == "path_denied"


def test_limits_apply_to_depth_count_and_size(tmp_path: Path) -> None:
    # Given: shallow, deep, and oversized files below the allowed root.
    root = tmp_path / "allowed"
    first = _write(root / "a.csv")
    _write(root / "b.pdf")
    _write(root / "deep" / "too-deep.docx")
    _write(root / "large.zip", "x" * 64)

    # When: strict limits are applied.
    result = discover_local_file_candidates(
        _config(root, ConfigOptions(max_depth=1, max_count=1, max_size_bytes=16)),
    )

    # Then: one deterministic candidate remains and every limit is audited.
    assert tuple(candidate.path for candidate in result.candidates) == (str(first.resolve()),)
    assert {event.event_type for event in result.audit} >= {
        "depth_limit_reached",
        "count_limit_reached",
        "size_limit_reached",
    }


def test_sorting_is_deterministic_independent_of_creation_order(tmp_path: Path) -> None:
    # Given: files created in reverse lexical order.
    root = tmp_path / "allowed"
    _write(root / "zeta.pdf")
    _write(root / "alpha.docx")
    _write(root / "middle.xlsx")

    # When: discovery runs twice with fresh result objects.
    first = discover_local_file_candidates(_config(root))
    second = discover_local_file_candidates(_config(root))

    # Then: candidates are sorted by normalized path, kind, and size.
    first_paths = [candidate.path for candidate in first.candidates]
    assert first_paths == sorted(first_paths)
    assert first_paths == [candidate.path for candidate in second.candidates]


def test_timeout_budget_stops_scan_and_records_partial_result(tmp_path: Path) -> None:
    # Given: a root and a clock that exhausts the budget immediately after start.
    root = tmp_path / "allowed"
    _write(root / "a.xlsx")
    _write(root / "b.docx")

    # When: discovery runs with a tiny timeout budget.
    result = discover_local_file_candidates(
        _config(root, ConfigOptions(timeout_seconds=0.1, clock=StepClock(step=0.2))),
    )

    # Then: the result is safe, partial, and explicitly audited.
    assert result.success is False
    assert result.timed_out is True
    assert result.candidates == ()
    assert result.audit[-1].event_type == "timeout_reached"


@pytest.mark.parametrize(
    ("filename", "kind", "minimum_confidence", "reason_fragment"),
    [
        ("sheet.xlsx", FileKind.EXCEL, 0.8, "spreadsheet"),
        ("legacy.xls", FileKind.EXCEL, 0.8, "spreadsheet"),
        ("export.csv", FileKind.EXCEL, 0.7, "spreadsheet"),
        ("letter.docx", FileKind.WORD, 0.8, "word"),
        ("legacy.doc", FileKind.WORD, 0.75, "word"),
        ("packet.pdf", FileKind.PDF, 0.8, "pdf"),
        ("slides.pptx", FileKind.PPT, 0.8, "presentation"),
        ("legacy.ppt", FileKind.PPT, 0.75, "presentation"),
        ("photo.png", FileKind.IMAGE, 0.7, "image"),
        ("bundle.zip", FileKind.ARCHIVE, 0.6, "archive"),
        ("unknown.bin", FileKind.UNKNOWN, 0.0, "unknown"),
    ],
)
def test_kind_and_confidence_hints_cover_supported_file_families(
    tmp_path: Path,
    filename: str,
    kind: FileKind,
    minimum_confidence: float,
    reason_fragment: str,
) -> None:
    # Given: one file for a supported extension family.
    root = tmp_path / "allowed"
    _write(root / filename)

    # When: candidates are discovered.
    result = discover_local_file_candidates(_config(root))

    # Then: kind, confidence, and reason are derived from metadata only.
    candidate = result.candidates[0]
    assert candidate.kind is kind
    assert candidate.confidence >= minimum_confidence
    assert reason_fragment in candidate.reason

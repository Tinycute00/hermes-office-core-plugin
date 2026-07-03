from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .local_file_candidates import (
    CandidateAuditEvent,
    CandidateDenial,
    CandidateDiscoveryConfig,
    CandidateDiscoveryResult,
    LocalFileCandidate,
    MonotonicClock,
    build_candidate,
)
from .safe_path import resolve_and_check_within_root


@dataclass(frozen=True, slots=True)
class ScanBudget:
    clock: MonotonicClock
    started_at: float
    timeout_seconds: float

    def expired(self) -> bool:
        return self.clock() - self.started_at >= self.timeout_seconds


@dataclass(frozen=True, slots=True)
class WorkItem:
    path: Path
    root: Path
    depth: int
    escape_reason: str


@dataclass(slots=True)
class ScanState:
    config: CandidateDiscoveryConfig
    roots: tuple[Path, ...]
    budget: ScanBudget
    candidates: list[LocalFileCandidate]
    denials: list[CandidateDenial]
    audit: list[CandidateAuditEvent]
    timed_out: bool = False
    count_limit_recorded: bool = False


def discover_local_file_candidates(config: CandidateDiscoveryConfig) -> CandidateDiscoveryResult:
    audit = [
        CandidateAuditEvent(
            "candidate_discovery_started",
            "started",
            "explicit allowed roots supplied",
        ),
    ]
    denials: list[CandidateDenial] = []
    roots = _allowed_roots(config, denials, audit)
    if not roots or _has_invalid_limits(config, denials, audit):
        return _result((), denials, audit, timed_out=False)

    state = ScanState(
        config=config,
        roots=roots,
        budget=ScanBudget(config.clock, config.clock(), config.timeout_seconds),
        candidates=[],
        denials=denials,
        audit=audit,
    )
    if config.requested_paths:
        for item in _requested_items(config.requested_paths, roots):
            _scan_item(state, item)
    else:
        for root in roots:
            _scan_item(state, WorkItem(root, root, 0, "symlink_escape"))
    return _result(
        tuple(sorted(state.candidates, key=lambda item: (item.path, item.kind.value, item.size))),
        state.denials,
        state.audit,
        timed_out=state.timed_out,
    )


def _allowed_roots(
    config: CandidateDiscoveryConfig,
    denials: list[CandidateDenial],
    audit: list[CandidateAuditEvent],
) -> tuple[Path, ...]:
    roots: list[Path] = []
    for raw_root in config.allowed_roots:
        root = Path(raw_root).expanduser()
        try:
            resolved = root.resolve(strict=True)
        except OSError:
            _deny(denials, audit, str(root), "allowed_root_unavailable")
            continue
        if resolved.is_dir():
            roots.append(resolved)
        else:
            _deny(denials, audit, str(resolved), "allowed_root_not_directory")
    if not roots:
        _deny(denials, audit, "", "allowed_roots_required")
    return tuple(sorted(set(roots), key=str))


def _has_invalid_limits(
    config: CandidateDiscoveryConfig,
    denials: list[CandidateDenial],
    audit: list[CandidateAuditEvent],
) -> bool:
    invalid = False
    for field, value in (
        ("max_depth", config.max_depth),
        ("max_count", config.max_count),
        ("max_size_bytes", config.max_size_bytes),
    ):
        if value < 1:
            _deny(denials, audit, field, "limit_must_be_positive")
            invalid = True
    if config.timeout_seconds <= 0:
        _deny(denials, audit, "timeout_seconds", "limit_must_be_positive")
        invalid = True
    return invalid


def _requested_items(
    raw_paths: tuple[Path | str, ...],
    roots: tuple[Path, ...],
) -> tuple[WorkItem, ...]:
    items: list[WorkItem] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_absolute():
            root = _matching_root(path.resolve(strict=False), roots) or roots[0]
            items.append(WorkItem(path, root, 0, "path_outside_allowed_roots"))
        else:
            items.extend(
                WorkItem(root / path, root, 0, "path_outside_allowed_roots") for root in roots
            )
    return tuple(items)


def _scan_item(state: ScanState, item: WorkItem) -> None:
    if state.timed_out:
        return
    if state.budget.expired():
        _timeout(state)
        return
    resolved = resolve_and_check_within_root(item.path, state.roots)
    if resolved is None:
        _deny(state.denials, state.audit, str(item.path), item.escape_reason)
        return
    if item.depth > state.config.max_depth:
        state.audit.append(
            CandidateAuditEvent(
                "depth_limit_reached",
                "skipped",
                "max depth reached",
                str(resolved),
            ),
        )
        return
    if resolved.is_dir():
        _scan_directory(state, WorkItem(resolved, item.root, item.depth, item.escape_reason))
        return
    if resolved.is_file():
        _add_file_candidate(state, resolved)


def _scan_directory(state: ScanState, item: WorkItem) -> None:
    try:
        children = tuple(sorted(item.path.iterdir(), key=lambda child: str(child).casefold()))
    except OSError:
        _deny(state.denials, state.audit, str(item.path), "path_unavailable")
        return
    for child in children:
        _scan_item(state, WorkItem(child, item.root, item.depth + 1, "symlink_escape"))
        if state.timed_out:
            return


def _add_file_candidate(state: ScanState, path: Path) -> None:
    try:
        file_stat = path.stat()
    except OSError:
        _deny(state.denials, state.audit, str(path), "path_unavailable")
        return
    if file_stat.st_size > state.config.max_size_bytes:
        state.audit.append(
            CandidateAuditEvent("size_limit_reached", "skipped", "max size reached", str(path)),
        )
        return
    if len(state.candidates) >= state.config.max_count:
        if not state.count_limit_recorded:
            state.audit.append(
                CandidateAuditEvent("count_limit_reached", "skipped", "max count reached"),
            )
            state.count_limit_recorded = True
        return
    state.candidates.append(build_candidate(path, file_stat, state.config))


def _within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _matching_root(path: Path, roots: tuple[Path, ...]) -> Path | None:
    for root in roots:
        if path == root or path.is_relative_to(root):
            return root
    return None


def _timeout(state: ScanState) -> None:
    state.timed_out = True
    state.audit.append(CandidateAuditEvent("timeout_reached", "partial", "timeout budget reached"))


def _deny(
    denials: list[CandidateDenial],
    audit: list[CandidateAuditEvent],
    path: str,
    reason: str,
) -> None:
    denials.append(CandidateDenial(path, reason))
    audit.append(CandidateAuditEvent("path_denied", "denied", reason, path or None))


def _result(
    candidates: tuple[LocalFileCandidate, ...],
    denials: list[CandidateDenial],
    audit: list[CandidateAuditEvent],
    *,
    timed_out: bool,
) -> CandidateDiscoveryResult:
    return CandidateDiscoveryResult(
        success=not denials and not timed_out,
        candidates=candidates,
        denials=tuple(denials),
        audit=tuple(audit),
        timed_out=timed_out,
    )

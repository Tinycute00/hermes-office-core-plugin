from __future__ import annotations

import os
from pathlib import Path
import stat
import time
from typing import Iterable, TypedDict


CANDIDATE_DIRECTORY = "officecli-candidates"
MAX_CANDIDATE_FILES = 32
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CANDIDATE_AGE_SECONDS = 24 * 60 * 60
MAX_RECEIPT_PATHS = 128


class CandidateLifecycleError(RuntimeError):
    pass


class CandidateCleanupResult(TypedDict):
    removed_count: int
    removed: list[str]
    remaining_files: int
    remaining_bytes: int
    skipped_links: list[str]


def is_linklike(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def candidate_root(data_root: Path) -> Path:
    return data_root / CANDIDATE_DIRECTORY


def validated_root(data_root: Path) -> Path | None:
    if os.path.lexists(data_root):
        if is_linklike(data_root) or not data_root.is_dir():
            raise CandidateLifecycleError(
                "Managed OfficeCLI plugin data root is linked or invalid."
            )
    root = candidate_root(data_root)
    if not os.path.lexists(root):
        return None
    if is_linklike(root) or not root.is_dir():
        raise CandidateLifecycleError("Managed OfficeCLI candidate root is linked or invalid.")
    resolved_data = data_root.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    try:
        resolved_root.relative_to(resolved_data)
    except ValueError as error:
        raise CandidateLifecycleError(
            "Managed OfficeCLI candidate root escapes plugin data."
        ) from error
    return resolved_root


def contained(root: Path, candidate: Path) -> bool:
    try:
        return candidate.relative_to(root) != Path(".")
    except ValueError:
        return False


def inventory(root: Path) -> tuple[list[tuple[int, int, Path]], list[Path], list[Path]]:
    files: list[tuple[int, int, Path]] = []
    directories: list[Path] = []
    links: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except OSError as error:
            raise CandidateLifecycleError(f"Cannot inspect managed candidates: {error}") from error
        for child in children:
            if is_linklike(child):
                links.append(child)
                continue
            try:
                status = child.lstat()
            except OSError as error:
                raise CandidateLifecycleError(
                    f"Cannot inspect managed candidate entry: {error}"
                ) from error
            if stat.S_ISDIR(status.st_mode):
                directories.append(child)
                pending.append(child)
            elif stat.S_ISREG(status.st_mode):
                if status.st_nlink > 1:
                    raise CandidateLifecycleError(
                        "Managed OfficeCLI candidate files must not be hard linked."
                    )
                files.append((status.st_mtime_ns, status.st_size, child))
            else:
                raise CandidateLifecycleError(
                    "Managed OfficeCLI candidate entries must be ordinary files or directories."
                )
    return files, directories, links


def remove_link_entry(path: Path) -> None:
    try:
        status = path.lstat()
        junction = hasattr(path, "is_junction") and path.is_junction()
        if junction or stat.S_ISDIR(status.st_mode):
            path.rmdir()
        else:
            path.unlink()
    except OSError as error:
        raise CandidateLifecycleError(
            f"Managed candidate link could not be removed: {error}"
        ) from error


def prune_managed_candidates(
    data_root: Path,
    *,
    older_than_seconds: int = MAX_CANDIDATE_AGE_SECONDS,
    preserve: Iterable[Path] = (),
) -> CandidateCleanupResult:
    root = validated_root(data_root)
    if root is None:
        return {
            "removed_count": 0,
            "removed": [],
            "remaining_files": 0,
            "remaining_bytes": 0,
            "skipped_links": [],
        }
    preserved = tuple({
        path.resolve(strict=False)
        for path in preserve
        if contained(root, path.resolve(strict=False))
    })

    def is_preserved(path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(
            resolved == item or contained(item, resolved)
            for item in preserved
        )

    def touches_preserved(path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(
            resolved == item
            or contained(item, resolved)
            or contained(resolved, item)
            for item in preserved
        )
    files, directories, links = inventory(root)
    removed: list[str] = []
    removed_count = 0
    for link in sorted(links, key=os.fspath):
        remove_link_entry(link)
        removed_count += 1
        if len(removed) < MAX_RECEIPT_PATHS:
            removed.append(os.fspath(link))
    cutoff_ns = time.time_ns() - max(0, older_than_seconds) * 1_000_000_000
    remove = {
        path
        for modified, _size, path in files
        if modified <= cutoff_ns and not is_preserved(path)
    }
    remaining = [item for item in files if item[2] not in remove]
    remaining_count = len(remaining)
    remaining_bytes = sum(size for _modified, size, _path in remaining)
    for modified, size, path in sorted(remaining, key=lambda item: (item[0], os.fspath(item[2]))):
        if remaining_count <= MAX_CANDIDATE_FILES and remaining_bytes <= MAX_CANDIDATE_BYTES:
            break
        if is_preserved(path):
            continue
        remove.add(path)
        remaining_count -= 1
        remaining_bytes -= size
    failed_removals = 0
    for path in sorted(remove, key=os.fspath):
        try:
            path.unlink()
        except OSError:
            failed_removals += 1
            continue
        removed_count += 1
        if len(removed) < MAX_RECEIPT_PATHS:
            removed.append(os.fspath(path))
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        if touches_preserved(directory):
            continue
        try:
            directory.rmdir()
        except OSError:
            continue
    live, _directories, live_links = inventory(root)
    live_bytes = sum(size for _modified, size, _path in live)
    if (
        failed_removals
        or live_links
        or len(live) > MAX_CANDIDATE_FILES
        or live_bytes > MAX_CANDIDATE_BYTES
    ):
        raise CandidateLifecycleError("Managed OfficeCLI candidate limits could not be enforced.")
    return {
        "removed_count": removed_count,
        "removed": removed,
        "remaining_files": len(live),
        "remaining_bytes": live_bytes,
        "skipped_links": [],
    }


def remove_managed_candidate(data_root: Path, candidate: Path) -> bool:
    root = validated_root(data_root)
    lexical_root = candidate_root(data_root)
    lexical_candidate = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError:
        return False
    current = lexical_root
    for part in relative.parts:
        current /= part
        if os.path.lexists(current) and is_linklike(current):
            return False
    if root is None or not os.path.lexists(lexical_candidate):
        return False
    try:
        resolved = lexical_candidate.resolve(strict=True)
    except OSError:
        return False
    if not contained(root, resolved) or not resolved.is_file():
        return False
    if resolved.lstat().st_nlink > 1:
        raise CandidateLifecycleError(
            "Managed OfficeCLI candidate files must not be hard linked."
        )
    try:
        resolved.unlink()
    except OSError as error:
        raise CandidateLifecycleError(f"Managed candidate could not be removed: {error}") from error
    parent = resolved.parent
    while parent != root and contained(root, parent):
        if is_linklike(parent):
            raise CandidateLifecycleError("Managed candidate parent became linked.")
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True

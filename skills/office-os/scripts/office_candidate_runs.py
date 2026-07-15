from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import stat

from office_candidates import CandidateLifecycleError
from office_candidates import assert_candidate_quota
from office_candidates import candidate_root
from office_candidates import contained
from office_candidates import inventory
from office_candidates import is_linklike
from office_candidates import remove_link_entry
from office_candidates import validated_root


type StateValue = str | int | float | bool | None | list[StateValue] | dict[str, StateValue]


def _valid_run_id(run_id: str) -> bool:
    return len(run_id) == 32 and all(character in "0123456789abcdef" for character in run_id)


def _strip_equivalent_windows_extended_prefix(value: str) -> str:
    if os.name != "nt" or not value.startswith("\\\\?\\"):
        return value
    suffix = value[4:]
    if len(suffix) >= 3 and suffix[1] == ":" and suffix[2] in "\\/":
        return suffix
    if suffix[:4].casefold() == "unc\\":
        return "\\\\" + suffix[4:]
    return value


def _absolute_path(value: str | Path) -> Path:
    return Path(_strip_equivalent_windows_extended_prefix(os.path.abspath(os.fspath(value))))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))


def _has_linklike_ancestor(path: Path) -> bool:
    for component in (path, *path.parents):
        if os.path.lexists(component) and is_linklike(component):
            return True
    return False


def reserve_candidate_directory(data_root: Path, run_id: str) -> Path:
    if not _valid_run_id(run_id):
        raise CandidateLifecycleError("Managed candidate run identity is invalid.")
    if os.path.lexists(data_root):
        if is_linklike(data_root) or not data_root.is_dir():
            raise CandidateLifecycleError(
                "Managed OfficeCLI plugin data root is linked or invalid."
            )
    else:
        try:
            data_root.mkdir(parents=True)
        except OSError as error:
            raise CandidateLifecycleError(
                f"Managed OfficeCLI plugin data root could not be created: {error}"
            ) from error
    lexical_root = candidate_root(data_root)
    if os.path.lexists(lexical_root):
        if is_linklike(lexical_root) or not lexical_root.is_dir():
            raise CandidateLifecycleError(
                "Managed OfficeCLI candidate root is linked or invalid."
            )
    else:
        try:
            lexical_root.mkdir()
        except OSError as error:
            raise CandidateLifecycleError(
                f"Managed OfficeCLI candidate root could not be created: {error}"
            ) from error
    root = validated_root(data_root)
    if root is None:
        raise CandidateLifecycleError("Managed OfficeCLI candidate root is unavailable.")
    assert_candidate_quota(data_root)
    directory = lexical_root / run_id
    if os.path.lexists(directory):
        if is_linklike(directory) or not directory.is_dir():
            raise CandidateLifecycleError(
                "Managed OfficeCLI candidate run directory is linked or invalid."
            )
    else:
        try:
            directory.mkdir()
        except OSError as error:
            raise CandidateLifecycleError(
                f"Managed candidate run directory could not be created: {error}"
            ) from error
    resolved = directory.resolve(strict=True)
    if resolved.parent != root:
        raise CandidateLifecycleError(
            "Managed OfficeCLI candidate run directory escapes staging."
        )
    return resolved


def validated_active_run_directory(
    data_root: Path, state: Mapping[str, StateValue]
) -> Path | None:
    value = state.get("candidate_directory")
    if value is None:
        cleaned = (
            "candidate_directory" in state
            and state.get("candidate") is None
            and state.get("candidate_cleanup_error") is None
            and "candidate_cleanup_error" in state
        )
        if cleaned:
            return None
        raise CandidateLifecycleError(
            "Active run has no valid reserved candidate directory."
        )
    run_id = state.get("run_id")
    if not isinstance(value, str) or not isinstance(run_id, str) or not _valid_run_id(run_id):
        raise CandidateLifecycleError(
            "Active run has no valid reserved candidate directory."
        )
    lexical = _absolute_path(value)
    expected = _absolute_path(candidate_root(data_root) / run_id)
    root = validated_root(data_root)
    if (
        root is None
        or not os.path.lexists(lexical)
        or is_linklike(lexical)
        or not lexical.is_dir()
        or _has_linklike_ancestor(lexical)
        or not os.path.lexists(expected)
        or is_linklike(expected)
        or not expected.is_dir()
        or _has_linklike_ancestor(expected)
    ):
        raise CandidateLifecycleError(
            "Active run reserved candidate directory is linked, missing, or invalid."
        )
    resolved = _absolute_path(lexical.resolve(strict=True))
    expected_resolved = _absolute_path(expected.resolve(strict=True))
    if (
        root is None
        or not _same_path(resolved, expected_resolved)
        or not _same_path(resolved.parent, _absolute_path(root))
    ):
        raise CandidateLifecycleError(
            "Active run candidate directory is outside its reserved location."
        )
    return expected


def validated_run_candidate(
    data_root: Path, state: Mapping[str, StateValue], value: str | Path
) -> Path:
    directory = validated_active_run_directory(data_root, state)
    if directory is None:
        raise CandidateLifecycleError(
            "Active run no longer has a reserved candidate directory."
        )
    lexical = _absolute_path(value)
    if _has_linklike_ancestor(lexical):
        raise CandidateLifecycleError("Candidate path contains a link or reparse point.")
    try:
        status = lexical.lstat()
    except OSError as error:
        raise CandidateLifecycleError("Candidate file is missing or invalid.") from error
    if not stat.S_ISREG(status.st_mode):
        raise CandidateLifecycleError("Candidate must be an ordinary file.")
    if status.st_nlink > 1:
        raise CandidateLifecycleError("Candidate must not be hard linked.")
    resolved = _absolute_path(lexical.resolve(strict=True))
    resolved_directory = _absolute_path(directory.resolve(strict=True))
    if not contained(resolved_directory, resolved):
        raise CandidateLifecycleError(
            "Candidate is outside the active run's reserved candidate directory."
        )
    relative = resolved.relative_to(resolved_directory)
    if relative == Path("."):
        raise CandidateLifecycleError("Candidate must be an ordinary file.")
    candidate = directory.joinpath(*relative.parts)
    cursor = directory
    for part in relative.parts:
        cursor /= part
        if os.path.lexists(cursor) and is_linklike(cursor):
            raise CandidateLifecycleError("Candidate path contains a link or reparse point.")
    assert_candidate_quota(data_root)
    return candidate


def active_run_candidate_paths(
    data_root: Path, state: Mapping[str, StateValue]
) -> list[Path]:
    directory = validated_active_run_directory(data_root, state)
    if directory is None:
        return []
    value = state.get("candidate")
    if value is None:
        return [directory]
    if not isinstance(value, str):
        raise CandidateLifecycleError("Active run candidate path is invalid.")
    return [directory, validated_run_candidate(data_root, state, value)]


def remove_candidate_directory(data_root: Path, value: str | Path) -> bool:
    directory = _absolute_path(value)
    root = validated_root(data_root)
    if root is None or not os.path.lexists(directory):
        return False
    if is_linklike(directory):
        try:
            parent = _absolute_path(directory.parent.resolve(strict=True))
        except OSError:
            return False
        if _has_linklike_ancestor(directory.parent) or not _same_path(
            parent, _absolute_path(root)
        ):
            return False
        remove_link_entry(directory)
        return True
    if not directory.is_dir():
        raise CandidateLifecycleError(
            "Managed OfficeCLI candidate run directory is invalid."
        )
    if _has_linklike_ancestor(directory):
        raise CandidateLifecycleError(
            "Managed OfficeCLI candidate run directory is linked or invalid."
        )
    resolved = _absolute_path(directory.resolve(strict=True))
    resolved_root = _absolute_path(root)
    if not _same_path(resolved.parent, resolved_root) or not contained(resolved_root, resolved):
        raise CandidateLifecycleError(
            "Managed OfficeCLI candidate run directory escapes staging."
        )
    files, directories, links = inventory(resolved)
    for link in links:
        remove_link_entry(link)
    for _modified, _size, path in files:
        try:
            path.unlink()
        except OSError as error:
            raise CandidateLifecycleError(
                f"Managed candidate file could not be removed: {error}"
            ) from error
    for child in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            child.rmdir()
        except OSError as error:
            raise CandidateLifecycleError(
                f"Managed candidate directory could not be removed: {error}"
            ) from error
    try:
        resolved.rmdir()
    except OSError as error:
        raise CandidateLifecycleError(
            f"Managed candidate run directory could not be removed: {error}"
        ) from error
    return True

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys


STATE_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "office-os" / "scripts"
if os.fspath(STATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, os.fspath(STATE_SCRIPTS))
from office_state import StateLeafError, open_private_state_leaf
from office_state import unlink_private_state_leaf, validate_private_state_leaf


ACTIVE_STATUSES = {"grounding", "agreed", "executing", "validating", "publishing"}


class HookStateError(RuntimeError):
    pass


def validate_state_leaf(path: Path, description: str) -> os.stat_result | None:
    try:
        return validate_private_state_leaf(path, description)
    except StateLeafError as error:
        raise HookStateError(str(error)) from error


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
        raise HookStateError(str(error)) from error


def unlink_state_leaf(path: Path, description: str, *, missing_ok: bool = False) -> None:
    try:
        unlink_private_state_leaf(path, description, missing_ok=missing_ok)
    except StateLeafError as error:
        raise HookStateError(str(error)) from error


def canonical_workspace(cwd: str | None) -> str:
    base = cwd or os.getcwd()
    return os.path.normcase(os.path.realpath(os.path.abspath(base)))


def plugin_data_root() -> Path:
    configured = os.environ.get("PLUGIN_DATA")
    if not configured:
        raise HookStateError("Office OS requires the plugin-owned PLUGIN_DATA value.")
    if not os.path.isabs(configured):
        raise HookStateError("Office OS requires an absolute plugin-owned PLUGIN_DATA value.")
    # Keep the hook-injected spelling for user-visible context.  On Windows,
    # abspath()/GetFullPathName() can silently turn C:\\Users\\runneradmin into
    # its 8.3 alias, which would violate the exact-root contract even though it
    # reaches the same directory.
    return Path(os.path.normpath(configured))


def plugin_root() -> Path:
    return Path(
        os.environ.get("PLUGIN_ROOT")
        or os.environ.get("CLAUDE_PLUGIN_ROOT")
        or Path(__file__).resolve().parents[2]
    )


def is_linklike(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def validate_ordinary_ancestors(path: Path, description: str) -> None:
    for component in (*reversed(path.parents), path):
        if not os.path.lexists(component):
            continue
        if is_linklike(component) or not stat.S_ISDIR(component.lstat().st_mode):
            raise HookStateError(f"Office OS {description} is linked or invalid.")


def ensure_ordinary_directory(
    path: Path, description: str, create_parents: bool = False
) -> Path:
    validate_ordinary_ancestors(path, description)
    if not os.path.lexists(path):
        path.mkdir(parents=create_parents)
    validate_ordinary_ancestors(path, description)
    return path


def workspace_dir(cwd: str | None, *, create: bool = True) -> Path:
    canonical = canonical_workspace(cwd)
    workspace_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    data_root = plugin_data_root()
    workspaces = data_root / "workspaces"
    directory = workspaces / workspace_id
    if not create:
        validate_ordinary_ancestors(directory, "workspace state directory")
        return directory
    workspaces = ensure_ordinary_directory(
        ensure_ordinary_directory(data_root, "plugin data root") / "workspaces",
        "workspace state root",
    )
    return ensure_ordinary_directory(
        workspaces / workspace_id, "workspace state directory"
    )

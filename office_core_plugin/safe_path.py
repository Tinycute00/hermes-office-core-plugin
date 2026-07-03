from __future__ import annotations

from pathlib import Path
from typing import Final

from .registry_base import RegistryError

# Paths that state_root must never resolve to (or under) via symlink.
_SENSITIVE_PREFIXES: tuple[Path, ...] = tuple(
    Path(p)
    for p in (
        "/etc",
        "/var",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/opt",
        "/sys",
        "/proc",
        "/dev",
        "/boot",
    )
)
_STATE_ROOT_FIELD: Final = "state_root"


def _is_under_sensitive_prefix(path: Path) -> bool:
    """Return True if resolved path falls under a known sensitive system prefix."""
    resolved = path.resolve(strict=False)
    for prefix in _SENSITIVE_PREFIXES:
        if resolved == prefix or resolved.is_relative_to(prefix):
            return True
    return False


def _has_symlink_escape(
    path: Path,
    allowed_roots: tuple[Path, ...],
) -> bool:
    """Check whether *path* or any parent directory contains a symlink
    whose resolved target lies outside every directory in *allowed_roots*.

    Returns ``True`` if an escape is detected, ``False`` otherwise.
    """
    checked: set[Path] = set()
    for component in [path, *path.parents]:
        if component in checked:
            continue
        checked.add(component)
        if not component.is_symlink():
            continue
        try:
            target = component.resolve(strict=True)
        except OSError:
            # Unresolvable symlink is treated as an escape.
            return True
        if not any(
            target == root or target.is_relative_to(root) for root in allowed_roots
        ):
            return True
    return False


def resolve_and_check_within_root(
    path: Path,
    allowed_roots: tuple[Path, ...],
) -> Path | None:
    """Resolve *path* and verify it stays inside *allowed_roots*.

    If the path contains a symlink that escapes the allowed roots, or if
    the resolved path is not within any allowed root, this returns ``None``.
    Otherwise it returns the resolved :class:`~pathlib.Path`.
    """
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None

    if _has_symlink_escape(path, allowed_roots):
        return None

    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
        return None

    return resolved


def validate_state_root(state_root: Path | str) -> Path:
    """Validate that *state_root* does not escape via symlinks.

    Resolves the path, then walks upward checking every component for
    symlinks.  If a symlink resolves to a parent of its own containing
    directory (upward escape) or lands under a sensitive system prefix,
    a :class:`~office_core_plugin.registry_base.RegistryError` is raised.

    Returns the resolved path on success.
    """
    path = Path(state_root).expanduser()
    resolved = path.resolve(strict=False)

    # Check each component for symlink escape.
    for component in [path, *path.parents]:
        if not component.is_symlink():
            continue
        try:
            target = component.resolve(strict=True)
        except OSError as exc:
            raise RegistryError(
                _STATE_ROOT_FIELD,
                f"symlink_unresolvable:{component}:{exc}",
            ) from exc

        # Escape: target is outside the symlink's containing directory tree.
        component_parent = component.parent.resolve(strict=False)
        if not (target == component_parent or target.is_relative_to(component_parent)):
            raise RegistryError(
                _STATE_ROOT_FIELD,
                f"symlink_escape:{component}->{target}",
            )

        # Sensitive-prefix escape.
        if _is_under_sensitive_prefix(target):
            raise RegistryError(
                _STATE_ROOT_FIELD,
                f"symlink_sensitive_prefix:{component}->{target}",
            )

    return resolved

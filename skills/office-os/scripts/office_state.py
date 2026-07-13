from __future__ import annotations

import os
from pathlib import Path
import stat


class StateLeafError(RuntimeError):
    pass


def _is_linklike(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _validate_status(path: Path, status: os.stat_result, description: str) -> None:
    if _is_linklike(status) or not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise StateLeafError(
            f"{description} must be one private ordinary file: {path.name}."
        )


def validate_private_state_leaf(path: Path, description: str) -> os.stat_result | None:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateLeafError(f"Cannot inspect {description}: {error}") from error
    _validate_status(path, status, description)
    return status


def _open_flags(flags: int) -> int:
    return flags | getattr(os, "O_NOFOLLOW", 0)


def open_private_state_leaf(
    path: Path,
    flags: int,
    description: str,
    *,
    create: bool = False,
    exclusive: bool = False,
    mode: int = 0o600,
) -> int:
    access_flags = flags & ~(os.O_CREAT | os.O_EXCL)
    for _attempt in range(3):
        status = validate_private_state_leaf(path, description)
        if status is None:
            if not create:
                raise FileNotFoundError(path)
            try:
                descriptor = os.open(
                    path,
                    _open_flags(access_flags | os.O_CREAT | os.O_EXCL),
                    mode,
                )
            except FileExistsError:
                continue
        else:
            if exclusive:
                raise StateLeafError(f"{description} already exists: {path.name}.")
            try:
                descriptor = os.open(path, _open_flags(access_flags))
            except FileNotFoundError:
                continue
        try:
            descriptor_status = os.fstat(descriptor)
            _validate_status(path, descriptor_status, description)
            current = validate_private_state_leaf(path, description)
            if current is None or not os.path.samestat(descriptor_status, current):
                raise StateLeafError(f"{description} changed while opening: {path.name}.")
            return descriptor
        except (OSError, StateLeafError):
            os.close(descriptor)
            raise
    raise StateLeafError(f"{description} changed while opening: {path.name}.")


def ensure_private_state_leaf(path: Path, description: str) -> None:
    descriptor = open_private_state_leaf(
        path, os.O_RDWR, description, create=True
    )
    os.close(descriptor)


def unlink_private_state_leaf(
    path: Path, description: str, *, missing_ok: bool = False
) -> None:
    status = validate_private_state_leaf(path, description)
    if status is None:
        if missing_ok:
            return
        raise FileNotFoundError(path)
    try:
        path.unlink()
    except OSError as error:
        raise StateLeafError(f"Cannot remove {description}: {error}") from error

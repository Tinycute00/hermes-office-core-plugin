from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import time
from typing import Any, Iterator

from office_hooks.state import HookStateError, open_state_leaf, unlink_state_leaf
from office_hooks.state import validate_state_leaf


def read_json(path: Path, default: Any) -> Any:
    try:
        descriptor = open_state_leaf(path, os.O_RDONLY, "Office OS hook state")
    except FileNotFoundError:
        return default
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".office-os-{path.name}.{os.getpid()}.tmp")
    validate_state_leaf(path, "Office OS hook state")
    descriptor = open_state_leaf(
        temp_path,
        os.O_WRONLY,
        "Office OS hook temporary",
        create=True,
        exclusive=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        validate_state_leaf(temp_path, "Office OS hook temporary")
        validate_state_leaf(path, "Office OS hook state")
        os.replace(temp_path, path)
        validate_state_leaf(path, "Office OS hook state")
    except (OSError, TypeError, ValueError, HookStateError):
        if os.path.lexists(temp_path):
            unlink_state_leaf(temp_path, "Office OS hook temporary")
        raise


def cleanup_stale_temps(directory: Path, max_age_seconds: int = 3600) -> None:
    cutoff = time.time() - max_age_seconds
    for candidate in directory.glob(".office-os-*.tmp"):
        try:
            validate_state_leaf(candidate, "Office OS hook temporary")
            if candidate.is_file() and candidate.stat().st_mtime <= cutoff:
                unlink_state_leaf(candidate, "Office OS hook temporary")
        except OSError:
            continue


@contextlib.contextmanager
def state_lock(directory: Path) -> Iterator[bool]:
    lock_path = directory / "run-state.lock"
    descriptor = open_state_leaf(
        lock_path, os.O_RDWR, "Office OS hook lock", create=True
    )
    acquired = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        deadline = time.monotonic() + 0.25
        while not acquired:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.025)
        if acquired:
            owner = json.dumps(
                {"pid": os.getpid(), "timestamp": time.time()},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, owner)
            os.ftruncate(descriptor, len(owner))
            os.fsync(descriptor)
        yield acquired
    finally:
        if acquired:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

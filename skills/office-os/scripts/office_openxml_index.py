"""Bounded Open XML package access for Office OS indexing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import zipfile


@dataclass(frozen=True, slots=True)
class IndexPackageLimits:
    """Trusted indexing limits for an Open XML ZIP package."""

    max_archive_bytes: int
    max_members: int
    max_member_bytes: int
    max_uncompressed_bytes: int
    max_compression_ratio: int


class IndexPackageLimitError(RuntimeError):
    """Raised when an Open XML package exceeds indexing resource limits."""


def validate_index_package(
    path: Path,
    package: zipfile.ZipFile,
    limits: IndexPackageLimits,
) -> None:
    """Reject a ZIP package before any member content is read."""

    if path.stat().st_size > limits.max_archive_bytes:
        raise IndexPackageLimitError("Office package exceeds index limits: archive size.")

    members = package.infolist()
    if len(members) > limits.max_members:
        raise IndexPackageLimitError("Office package exceeds index limits: member count.")

    total_uncompressed = 0
    for member in members:
        if member.file_size > limits.max_member_bytes:
            raise IndexPackageLimitError("Office package exceeds index limits: member size.")
        if member.file_size and not member.compress_size:
            raise IndexPackageLimitError("Office package exceeds index limits: compression ratio.")
        if member.file_size > member.compress_size * limits.max_compression_ratio:
            raise IndexPackageLimitError("Office package exceeds index limits: compression ratio.")
        total_uncompressed += member.file_size
        if total_uncompressed > limits.max_uncompressed_bytes:
            raise IndexPackageLimitError("Office package exceeds index limits: total size.")


@contextmanager
def open_index_package(
    path: Path,
    limits: IndexPackageLimits,
) -> Iterator[zipfile.ZipFile]:
    """Open a ZIP package only after its archive metadata stays in bounds."""

    with zipfile.ZipFile(path) as package:
        validate_index_package(path, package, limits)
        yield package

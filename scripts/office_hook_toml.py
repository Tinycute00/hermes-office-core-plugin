from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tomllib


class TomlError(RuntimeError):
    pass


def require_regular_file(path: Path, description: str) -> None:
    if path.is_symlink():
        raise TomlError(f"{description} must be a regular file: {path}")
    if not path.exists():
        return
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise TomlError(f"{description} must be a regular, unlinked file: {path}")


def require_directory(path: Path, description: str, create: bool) -> None:
    if path.is_symlink():
        raise TomlError(f"{description} must not be a symlink: {path}")
    if not path.exists():
        if not create:
            return
        path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise TomlError(f"{description} must be a directory: {path}")


def atomic_write(path: Path, text: str, description: str) -> None:
    require_regular_file(path, description)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.office-os-{os.getpid()}.tmp")
    created = False
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        created = True
        os.replace(temporary, path)
    except OSError:
        if created and temporary.exists():
            temporary.unlink()
        raise


def read_toml(path: Path) -> str:
    require_regular_file(path, "Codex config")
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise TomlError(f"Codex config is not valid TOML: {path}") from error
    return text


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _section_name(line: str) -> str | None:
    match = re.match(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$", line.rstrip("\r\n"))
    return match.group(1).strip() if match else None


def _section_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    starts = [
        (index, name)
        for index, line in enumerate(lines)
        if (name := _section_name(line)) is not None
    ]
    return [
        (start, starts[index + 1][0] if index + 1 < len(starts) else len(lines), name)
        for index, (start, name) in enumerate(starts)
    ]


def _find_section(lines: list[str], name: str) -> tuple[int, int] | None:
    for start, end, current in _section_spans(lines):
        if current == name:
            return start, end
    return None


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    return "\n" if line.endswith("\n") else ""


def _ensure_line_ending(lines: list[str], index: int) -> None:
    if index >= 0 and not _line_ending(lines[index]):
        lines[index] += "\n"


def _set_features_hooks(lines: list[str]) -> None:
    section = _find_section(lines, "features")
    if section is None:
        if lines:
            _ensure_line_ending(lines, len(lines) - 1)
            if lines[-1].strip():
                lines.append("\n")
        lines.extend(["[features]\n", "hooks = true\n"])
        return
    start, end = section
    for index in range(start + 1, end):
        content = lines[index].rstrip("\r\n")
        match = re.match(r"^(\s*hooks\s*=\s*)(.*)$", content)
        if match:
            value = match.group(2)
            comment = value[value.find("#") - 1 :] if "#" in value else ""
            lines[index] = f"{match.group(1)}true{comment}{_line_ending(lines[index])}"
            return
    _ensure_line_ending(lines, start)
    lines.insert(start + 1, "hooks = true\n")


def _trusted_hash_line(line: str) -> tuple[bool, str | None]:
    match = re.match(
        r'^\s*trusted_hash\s*=\s*("(?:\\.|[^"\\])*")\s*(?:#.*)?$',
        line.rstrip("\r\n"),
    )
    if match is None:
        return False, None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return True, None
    return True, value if isinstance(value, str) else None


def _state_section(lines: list[str], key: str) -> tuple[int, int] | None:
    return _find_section(lines, f"hooks.state.{_toml_string(key)}")


def _remove_owned(lines: list[str], states: dict[str, str]) -> None:
    removals: list[tuple[int, int, list[str]]] = []
    for key, expected in states.items():
        span = _state_section(lines, key)
        if span is None:
            continue
        start, end = span
        indexes: list[int] = []
        values: list[str | None] = []
        for index in range(start + 1, end):
            is_hash, value = _trusted_hash_line(lines[index])
            if is_hash:
                indexes.append(index)
                values.append(value)
        if not indexes or any(value != expected for value in values):
            continue
        remaining = [
            lines[index] for index in range(start + 1, end) if index not in indexes
        ]
        content = any(line.strip() and not line.lstrip().startswith("#") for line in remaining)
        removals.append((start, end, remaining if content else []))
    for start, end, remaining in sorted(removals, reverse=True):
        if remaining:
            lines[start + 1 : end] = remaining
        else:
            del lines[start:end]


def _upsert_state(lines: list[str], key: str, value: str) -> None:
    span = _state_section(lines, key)
    if span is None:
        if lines:
            _ensure_line_ending(lines, len(lines) - 1)
            if lines[-1].strip():
                lines.append("\n")
        lines.extend([
            f"[hooks.state.{_toml_string(key)}]\n",
            f"trusted_hash = {_toml_string(value)}\n",
        ])
        return
    start, end = span
    indexes = [
        index
        for index in range(start + 1, end)
        if _trusted_hash_line(lines[index])[0]
    ]
    line = f"trusted_hash = {_toml_string(value)}\n"
    if indexes:
        lines[indexes[0]] = line
        for index in reversed(indexes[1:]):
            del lines[index]
    else:
        if end > start:
            _ensure_line_ending(lines, end - 1)
        lines.insert(end, line)


def edit_codex_toml(
    text: str,
    owned_states: dict[str, str],
    desired_states: dict[str, str],
    enable_hooks: bool,
) -> str:
    lines = text.splitlines(keepends=True)
    if enable_hooks:
        _set_features_hooks(lines)
    _remove_owned(lines, owned_states)
    for key, value in desired_states.items():
        _upsert_state(lines, key, value)
    result = "".join(lines)
    try:
        tomllib.loads(result)
    except tomllib.TOMLDecodeError as error:
        raise TomlError("edited Codex config is not valid TOML") from error
    return result

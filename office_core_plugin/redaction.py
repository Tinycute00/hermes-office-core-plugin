from __future__ import annotations

import re
from typing import Final, TypeAlias

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

REDACTED: Final = "[REDACTED]"
AUTHORIZATION_FIELD_PATTERN_SOURCE: Final = r"proxy[_-]?authorization|authorization"
REDACTION_FIELD_PATTERN_SOURCE: Final = (
    rf"secret|token|password|api[_-]?key|{AUTHORIZATION_FIELD_PATTERN_SOURCE}|credential"
)
CREDENTIAL_VALUE_PATTERN_SOURCE: Final = r"[A-Za-z0-9][A-Za-z0-9._~+/\-=]{11,}"
SECRET_TEXT_PATTERN: Final = re.compile(
    rf"""(?ix)
    (?:
        ['"]?\b(?:{AUTHORIZATION_FIELD_PATTERN_SOURCE})\b['"]?
        \s*(?:=|:)\s*
        (?:
            '[^']*'
            | "[^"]*"
            | (?:bearer|basic)\s+[^,\s;}}\]]+
            | [^,\s;}}\]]+
        )
        |
        ['"]?\b(?:{REDACTION_FIELD_PATTERN_SOURCE})\b['"]?
        \s*(?:=|:)\s*
        (?:
            '[^']*'
            | "[^"]*"
            | [^,\s;}}\]]+
        )
    )
    """,
)
FREE_TEXT_SECRET_PATTERN: Final = re.compile(
    rf"""(?ix)
    (?:
        \b(?:bearer|basic)\s+{CREDENTIAL_VALUE_PATTERN_SOURCE}
        |
        \b(?:api[_\-\s]?(?:key|token)|access[_\-\s]?token|auth[_\-\s]?token)
        \s+(?:is\s+)?{CREDENTIAL_VALUE_PATTERN_SOURCE}
        |
        \b(?:sk|pk)-[A-Za-z0-9][A-Za-z0-9._-]{{10,}}\b
        |
        \b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{{20,}}\b
        |
        \bgithub_pat_[A-Za-z0-9_]{{20,}}\b
        |
        \bAIza[A-Za-z0-9_-]{{20,}}\b
        |
        \bya29\.[A-Za-z0-9_-]{{20,}}\b
    )
    """,
)
SECRET_KEY_PATTERN: Final = re.compile(
    rf"(?i)({REDACTION_FIELD_PATTERN_SOURCE})",
)
PATH_SEPARATOR_PATTERN: Final = re.compile(r"([\\/]+)")


def redact_json(value: JSONValue) -> JSONValue:
    return _redact_json_value(value, set())


def _redact_json_value(value: JSONValue, seen: set[int]) -> JSONValue:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        value_id = id(value)
        if value_id in seen:
            return REDACTED
        seen.add(value_id)
        try:
            return [_redact_json_value(item, seen) for item in value]
        finally:
            seen.remove(value_id)
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in seen:
            return REDACTED
        seen.add(value_id)
        try:
            return {
                key: REDACTED if SECRET_KEY_PATTERN.search(key) else _redact_json_value(item, seen)
                for key, item in value.items()
            }
        finally:
            seen.remove(value_id)
    return value


def redact_text(value: str) -> str:
    labelled_value = SECRET_TEXT_PATTERN.sub(REDACTED, value)
    return FREE_TEXT_SECRET_PATTERN.sub(REDACTED, labelled_value)


def redact_path_diagnostic(value: str) -> str:
    parts = PATH_SEPARATOR_PATTERN.split(value)
    return "".join(
        _redact_path_segment(part) if index % 2 == 0 else part
        for index, part in enumerate(parts)
    )


def _redact_path_segment(value: str) -> str:
    redacted_value = redact_text(value)
    if redacted_value != value or SECRET_KEY_PATTERN.search(value):
        return REDACTED
    return value

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
SECRET_KEY_PATTERN: Final = re.compile(
    rf"(?i)({REDACTION_FIELD_PATTERN_SOURCE})",
)


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
    return SECRET_TEXT_PATTERN.sub(REDACTED, value)

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias, TypedDict

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]
JSONTypeName: TypeAlias = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
    "null",
]
ExactJsonScalarType: TypeAlias = type[bool] | type[int] | type[float] | type[str]

SECRET_TEXT_PATTERN: Final = re.compile(
    r"(?i)\b(secret|token|password|api[_-]?key|authorization)\s*=\s*[^,\s;]+",
)
SECRET_KEY_PATTERN: Final = re.compile(
    r"(?i)(secret|token|password|api[_-]?key|authorization|credential)",
)
REDACTED: Final = "[REDACTED]"
ENVELOPE_KEYS: Final = ("success", "operation_id", "error", "warnings", "data")
EXACT_JSON_TYPE_NAMES: Final[dict[ExactJsonScalarType, JSONTypeName]] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
}


class SchemaSpec(TypedDict, total=False):
    type: JSONTypeName | tuple[JSONTypeName, ...] | list[JSONTypeName]
    required: tuple[str, ...] | list[str]
    properties: dict[str, SchemaSpec]
    items: SchemaSpec
    additionalProperties: bool


class EnvelopeError(TypedDict):
    code: str
    message: str


class HandlerEnvelope(TypedDict):
    success: bool
    operation_id: str
    error: EnvelopeError | None
    warnings: list[str]
    data: JSONValue


class ToolHandler(Protocol):
    def __call__(self, args: JSONObject) -> JSONValue: ...


class SafeToolHandler(Protocol):
    def __call__(self, args: JSONValue) -> str: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    schema: SchemaSpec
    handler: ToolHandler
    description: str


@dataclass(frozen=True, slots=True)
class SchemaValidationError(Exception):
    messages: tuple[str, ...]

    def __str__(self) -> str:
        return "; ".join(self.messages)


def wrap_handler(handler: ToolHandler, schema: SchemaSpec | None = None) -> SafeToolHandler:
    def wrapped(args: JSONValue) -> str:
        operation_id = _operation_id(handler, args)
        try:
            parsed_args = _parse_args(args)
            _validate_args(parsed_args, schema)
            data = _redact_json(handler(parsed_args))
            return _dump_envelope(
                {
                    "success": True,
                    "operation_id": operation_id,
                    "error": None,
                    "warnings": [],
                    "data": data,
                },
            )
        except SchemaValidationError as exc:
            return _failure_envelope(
                operation_id=operation_id,
                code="handler_validation_error",
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - plugin boundary must return JSON for every handler failure.
            return _failure_envelope(
                operation_id=operation_id,
                code="handler_runtime_error",
                message=str(exc),
            )

    return wrapped


def _failure_envelope(operation_id: str, code: str, message: str) -> str:
    return _dump_envelope(
        {
            "success": False,
            "operation_id": operation_id,
            "error": {
                "code": code,
                "message": _redact_text(message),
            },
            "warnings": [],
            "data": None,
        },
    )


def _parse_args(args: JSONValue) -> JSONObject:
    if isinstance(args, dict):
        return args
    raise SchemaValidationError((f"args must be an object, got {_json_type_name(args)}",))


def _validate_args(args: JSONObject, schema: SchemaSpec | None) -> None:
    if schema is None:
        return
    errors = _validate_schema_value("$", args, schema)
    if errors:
        raise SchemaValidationError(tuple(errors))


def _validate_schema_value(path: str, value: JSONValue, schema: SchemaSpec) -> list[str]:
    errors: list[str] = []
    expected_types = _expected_types(schema)
    if expected_types and _json_type_name(value) not in expected_types:
        expected = _format_expected_types(expected_types)
        actual = _json_type_name(value)
        return [f"{path} must be {expected}, got {actual}"]

    properties = schema.get("properties", {})
    required = schema.get("required", ())
    if properties or required or schema.get("additionalProperties") is False:
        if isinstance(value, dict):
            errors.extend(_validate_object_properties(path, value, schema))
        else:
            errors.append(f"{path} must be object, got {_json_type_name(value)}")

    item_schema = schema.get("items")
    if item_schema is not None and isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_validate_schema_value(f"{path}[{index}]", item, item_schema))
    return errors


def _validate_object_properties(path: str, value: JSONObject, schema: SchemaSpec) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    errors.extend(
        f"{path}.{key} is required" for key in schema.get("required", ()) if key not in value
    )

    for key, property_schema in properties.items():
        if key in value:
            errors.extend(_validate_schema_value(f"{path}.{key}", value[key], property_schema))

    if schema.get("additionalProperties") is False:
        allowed = set(properties)
        errors.extend(f"{path}.{key} is not allowed" for key in sorted(value) if key not in allowed)
    return errors


def _expected_types(schema: SchemaSpec) -> tuple[JSONTypeName, ...]:
    raw_type = schema.get("type")
    if raw_type is None:
        return ()
    if isinstance(raw_type, str):
        return (raw_type,)
    return tuple(raw_type)


def _format_expected_types(expected_types: tuple[JSONTypeName, ...]) -> str:
    return " or ".join(expected_types)


def _json_type_name(value: JSONValue) -> JSONTypeName:
    if value is None:
        return "null"
    exact_type_name = EXACT_JSON_TYPE_NAMES.get(type(value))
    if exact_type_name is not None:
        return exact_type_name
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _operation_id(handler: ToolHandler, args: JSONValue) -> str:
    handler_name = f"{handler.__module__}.{handler.__qualname__}"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", handler_name).strip("_")
    try:
        canonical_args = json.dumps(
            _redact_json(args),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        canonical_args = REDACTED
    digest = hashlib.sha256(f"{handler_name}:{canonical_args}".encode()).hexdigest()[:12]
    return f"{safe_name}:{digest}"


def _redact_json(value: JSONValue) -> JSONValue:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, dict):
        return {
            key: REDACTED if SECRET_KEY_PATTERN.search(key) else _redact_json(item)
            for key, item in value.items()
        }
    return value


def _redact_text(value: str) -> str:
    return SECRET_TEXT_PATTERN.sub(REDACTED, value)


def _dump_envelope(envelope: HandlerEnvelope) -> str:
    return json.dumps(
        {key: envelope[key] for key in ENVELOPE_KEYS},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
    )

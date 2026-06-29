from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final, assert_never

from .operation_policy import ConfidenceScoreError, confidence_band

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

SHA256_HEX_LENGTH: Final = 64


@unique
class TemplateClassification(StrEnum):
    SAME = "same"
    SIMILAR = "similar"
    REVISED = "revised"
    NEW = "new"
    RULE_BASED = "rule-based"


@unique
class OwnerConfirmationState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class RegistryError(Exception):
    field: str
    detail: str

    def __str__(self) -> str:
        return f"{self.field}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ProvenanceSource:
    source_type: str
    source_uri: str
    observed_at: str
    method: str


def classification(data: JSONObject, field: str) -> TemplateClassification:
    value = text(data, field)
    try:
        return TemplateClassification(value)
    except ValueError as exc:
        raise RegistryError(field, f"unsupported classification {value!r}") from exc


def confirmation_state(data: JSONObject, field: str) -> OwnerConfirmationState:
    value = text(data, field)
    try:
        return OwnerConfirmationState(value)
    except ValueError as exc:
        raise RegistryError(field, f"unsupported confirmation state {value!r}") from exc


def objects(data: JSONObject, field: str) -> tuple[JSONObject, ...]:
    value = data.get(field)
    if not isinstance(value, list):
        raise RegistryError(field, "must be a list")
    return tuple(ensure_object(field, item) for item in value)


def one_object(data: JSONObject, field: str) -> JSONObject:
    return ensure_object(field, data.get(field))


def ensure_object(field: str, value: JSONValue) -> JSONObject:
    if isinstance(value, dict):
        return value
    raise RegistryError(field, "must be an object")


def text_tuple(data: JSONObject, field: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list):
        raise RegistryError(field, "must be a list")
    return tuple(list_text(field, item) for item in value)


def list_text(field: str, value: JSONValue) -> str:
    if isinstance(value, str) and value.strip():
        return value
    raise RegistryError(field, "items must be non-empty strings")


def text(data: JSONObject, field: str) -> str:
    value = data.get(field)
    if isinstance(value, str) and value.strip():
        return value
    raise RegistryError(field, "required")


def optional_text(data: JSONObject, field: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    raise RegistryError(field, "must be a non-empty string or null")


def hash_text(data: JSONObject, field: str) -> str:
    value = text(data, field)
    if len(value) == SHA256_HEX_LENGTH:
        return value
    raise RegistryError(field, "must be a sha256 hex digest")


def confidence(data: JSONObject, field: str) -> float:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RegistryError(field, "must be a number")
    score = float(value)
    validate_confidence(score)
    return score


def validate_confidence(score: float) -> None:
    try:
        band = confidence_band(score)
    except ConfidenceScoreError as exc:
        field = "confidence"
        detail = f"{score!r} is outside 0.0-1.0"
        raise RegistryError(field, detail) from exc
    match band:
        case "low" | "medium" | "high":
            return
        case _ as unreachable:
            assert_never(unreachable)

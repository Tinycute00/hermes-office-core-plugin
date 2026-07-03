from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from .operation_policy import CONFIDENCE_HIGH_MIN, ConfidenceScoreError, confidence_band
from .redaction import redact_text

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

HEX_DIGEST_PATTERN: Final = re.compile(r"\A[0-9a-f]{64}\Z")
DEFAULT_SOURCE_FRESHNESS_CONSTRAINTS: Final = (
    "Owner confirms this source is current for the requested workflow before execution.",
)
DEFAULT_SOURCE_SELECTION_RULE: Final = "single_high_confidence_source_or_owner_confirmation"


@dataclass(frozen=True, slots=True)
class TaskContractError(Exception):
    field: str
    detail: str

    def __str__(self) -> str:
        return f"{redact_text(self.field)}: {redact_text(self.detail)}"


@dataclass(frozen=True, slots=True)
class SourceRequirement:
    label: str
    uri: str
    evidence_hash: str
    summary: str
    freshness_constraints: tuple[str, ...] = DEFAULT_SOURCE_FRESHNESS_CONSTRAINTS
    selection_rule: str = DEFAULT_SOURCE_SELECTION_RULE
    minimum_confidence: float = CONFIDENCE_HIGH_MIN

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        freshness_constraints = DEFAULT_SOURCE_FRESHNESS_CONSTRAINTS
        if "freshness_constraints" in data:
            freshness_constraints = _text_tuple(data, "freshness_constraints")
        selection_rule = DEFAULT_SOURCE_SELECTION_RULE
        if "selection_rule" in data:
            selection_rule = _text(data, "selection_rule")
        minimum_confidence = CONFIDENCE_HIGH_MIN
        if "minimum_confidence" in data:
            minimum_confidence = _confidence(data, "minimum_confidence")
        return cls(
            label=_text(data, "label"),
            uri=_text(data, "uri"),
            evidence_hash=_hash_text(data, "evidence_hash"),
            summary=_text(data, "summary"),
            freshness_constraints=freshness_constraints,
            selection_rule=selection_rule,
            minimum_confidence=minimum_confidence,
        )

    def to_dict(self) -> JSONObject:
        _require_text("label", self.label)
        _require_text("uri", self.uri)
        _require_hash("evidence_hash", self.evidence_hash)
        _require_text("summary", self.summary)
        confidence_band(self.minimum_confidence)
        return {
            "evidence_hash": self.evidence_hash,
            "freshness_constraints": _redacted_required_tuple(
                "freshness_constraints",
                self.freshness_constraints,
            ),
            "label": redact_text(self.label),
            "minimum_confidence": self.minimum_confidence,
            "selection_rule": redact_text(_required_attr("selection_rule", self.selection_rule)),
            "summary": redact_text(self.summary),
            "uri": redact_text(self.uri),
        }


@dataclass(frozen=True, slots=True)
class CorrectnessCriteria:
    structure_openability: tuple[str, ...]
    data_provenance: tuple[str, ...]
    format_template: tuple[str, ...]
    delivery_approval: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            structure_openability=_text_tuple(data, "structure_openability"),
            data_provenance=_text_tuple(data, "data_provenance"),
            format_template=_text_tuple(data, "format_template"),
            delivery_approval=_text_tuple(data, "delivery_approval"),
        )

    def to_dict(self) -> JSONObject:
        return {
            "structure_openability": _redacted_required_tuple(
                "structure_openability",
                self.structure_openability,
            ),
            "data_provenance": _redacted_required_tuple("data_provenance", self.data_provenance),
            "format_template": _redacted_required_tuple("format_template", self.format_template),
            "delivery_approval": _redacted_required_tuple(
                "delivery_approval",
                self.delivery_approval,
            ),
        }


@dataclass(frozen=True, slots=True)
class OfficeTaskContract:
    contract_id: str
    workflow_type: str
    deliverable_type: str
    intended_audience: str
    unresolved_questions: tuple[str, ...]
    source_requirements: tuple[SourceRequirement, ...]
    correctness_criteria: CorrectnessCriteria
    risk: str
    owner_confirmations: tuple[str, ...]
    validation_plan: tuple[str, ...]
    bridge_target: str
    mode: str
    provenance: tuple[str, ...]
    confidence: float
    next_step: str

    def __post_init__(self) -> None:
        confidence_band(self.confidence)

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            contract_id=_text(data, "contract_id"),
            workflow_type=_text(data, "workflow_type"),
            deliverable_type=_text(data, "deliverable_type"),
            intended_audience=_text(data, "intended_audience"),
            unresolved_questions=_text_tuple(data, "unresolved_questions"),
            source_requirements=_source_requirements(data),
            correctness_criteria=CorrectnessCriteria.from_dict(
                _object(data, "correctness_criteria"),
            ),
            risk=_text(data, "risk"),
            owner_confirmations=_text_tuple(data, "owner_confirmations"),
            validation_plan=_text_tuple(data, "validation_plan"),
            bridge_target=_text(data, "bridge_target"),
            mode=_text(data, "mode"),
            provenance=_text_tuple(data, "provenance"),
            confidence=_confidence(data, "confidence"),
            next_step=_text(data, "next_step"),
        )

    def to_dict(self) -> JSONObject:
        return {
            "bridge_target": redact_text(self.bridge_target),
            "confidence": {
                "band": confidence_band(self.confidence),
                "score": self.confidence,
            },
            "contract_id": redact_text(_required_attr("contract_id", self.contract_id)),
            "correctness_criteria": self.correctness_criteria.to_dict(),
            "deliverable_type": redact_text(
                _required_attr("deliverable_type", self.deliverable_type),
            ),
            "intended_audience": redact_text(
                _required_attr("intended_audience", self.intended_audience),
            ),
            "mode": redact_text(_required_attr("mode", self.mode)),
            "next_step": redact_text(_required_attr("next_step", self.next_step)),
            "owner_confirmations": _redacted_required_tuple(
                "owner_confirmations",
                self.owner_confirmations,
            ),
            "provenance": _redacted_required_tuple("provenance", self.provenance),
            "risk": redact_text(_required_attr("risk", self.risk)),
            "source_requirements": [item.to_dict() for item in self.source_requirements],
            "unresolved_questions": _redacted_required_tuple(
                "unresolved_questions",
                self.unresolved_questions,
            ),
            "validation_plan": _redacted_required_tuple("validation_plan", self.validation_plan),
            "workflow_type": redact_text(_required_attr("workflow_type", self.workflow_type)),
        }


def _source_requirements(data: JSONObject) -> tuple[SourceRequirement, ...]:
    values = _objects(data, "source_requirements")
    if not values:
        field = "source_requirements"
        detail = "at least one source is required"
        raise TaskContractError(field, detail)
    return tuple(SourceRequirement.from_dict(item) for item in values)


def _objects(data: JSONObject, field: str) -> tuple[JSONObject, ...]:
    value = _required_value(data, field)
    if not isinstance(value, list):
        raise TaskContractError(field, "must be an array")
    items: list[JSONObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise TaskContractError(field, "entries must be objects")
        items.append(item)
    return tuple(items)


def _object(data: JSONObject, field: str) -> JSONObject:
    value = _required_value(data, field)
    if isinstance(value, dict):
        return value
    raise TaskContractError(field, "must be an object")


def _text(data: JSONObject, field: str) -> str:
    value = _required_value(data, field)
    if isinstance(value, str):
        _require_text(field, value)
        return value
    raise TaskContractError(field, "must be a string")


def _hash_text(data: JSONObject, field: str) -> str:
    value = _text(data, field)
    _require_hash(field, value)
    return value


def _text_tuple(data: JSONObject, field: str) -> tuple[str, ...]:
    value = _required_value(data, field)
    if not isinstance(value, list):
        raise TaskContractError(field, "must be an array")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TaskContractError(field, "entries must be strings")
        _require_text(field, item)
        items.append(item)
    if not items:
        raise TaskContractError(field, "at least one entry is required")
    return tuple(items)


def _confidence(data: JSONObject, field: str) -> float:
    value = _required_value(data, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskContractError(field, "must be a number")
    score = float(value)
    try:
        confidence_band(score)
    except ConfidenceScoreError as exc:
        raise TaskContractError(field, "must be between 0.0 and 1.0") from exc
    return score


def _required_value(data: JSONObject, field: str) -> JSONValue:
    if field in data:
        return data[field]
    raise TaskContractError(field, "required")


def _required_attr(field: str, value: str) -> str:
    _require_text(field, value)
    return value


def _require_text(field: str, value: str) -> None:
    if value.strip():
        return
    raise TaskContractError(field, "required")


def _require_hash(field: str, value: str) -> None:
    if HEX_DIGEST_PATTERN.fullmatch(value):
        return
    raise TaskContractError(field, "must be a sha256 hex digest")


def _redacted_required_tuple(field: str, values: tuple[str, ...]) -> list[JSONValue]:
    if not values:
        raise TaskContractError(field, "at least one entry is required")
    return [redact_text(_required_attr(field, value)) for value in values]

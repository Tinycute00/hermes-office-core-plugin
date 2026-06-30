from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final, Protocol, assert_never

from .redaction import redact_json, redact_text

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

CONFIDENCE_LOW_MAX: Final = 0.50
CONFIDENCE_HIGH_MIN: Final = 0.80


@unique
class OperationKind(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXTERNAL_SEND = "external_send"


@unique
class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@unique
class ConfirmationState(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    CONFIRMED = "confirmed"

    @classmethod
    def parse(cls, value: str) -> ConfirmationState:
        try:
            return cls(value)
        except ValueError as exc:
            field = "confirmation_state"
            detail = f"unsupported value {value!r}"
            raise OperationPolicyError(field, detail) from exc


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class OperationPolicyError(Exception):
    field: str
    detail: str

    def __str__(self) -> str:
        return f"{self.field}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ConfidenceScoreError(OperationPolicyError):
    score: float

    def __init__(self, score: float) -> None:
        OperationPolicyError.__init__(self, "confidence", f"{score!r} is outside 0.0-1.0")
        object.__setattr__(self, "score", score)


class OperationExecutor(Protocol):
    def __call__(self) -> JSONValue: ...


@dataclass(frozen=True, slots=True)
class OperationFlags:
    read: bool = False
    write: bool = False
    delete: bool = False
    external_send: bool = False

    @property
    def is_high_impact(self) -> bool:
        return self.write or self.delete or self.external_send

    def to_json(self) -> JSONObject:
        return {
            "read": self.read,
            "write": self.write,
            "delete": self.delete,
            "external_send": self.external_send,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceInput:
    source_type: str
    source_uri: str
    observed_at: str
    method: str
    content: str | None = None

    def to_record(self) -> JSONObject:
        _require_text("source_type", self.source_type)
        _require_text("source_uri", self.source_uri)
        _require_text("observed_at", self.observed_at)
        _require_text("method", self.method)
        evidence_hash = ""
        if self.content is not None:
            evidence_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return {
            "source_type": redact_text(self.source_type),
            "source_uri": redact_text(self.source_uri),
            "observed_at": redact_text(self.observed_at),
            "method": redact_text(self.method),
            "evidence_hash": evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class OperationRequest:
    kind: OperationKind
    risk_level: RiskLevel
    label: str
    flags: OperationFlags
    confirmation_state: ConfirmationState
    confidence: float
    provenance: tuple[ProvenanceInput, ...]

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class AuditOutcome:
    event_type: str
    outcome: str
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    success: bool
    requires_confirmation: bool
    draft_only: bool
    data: JSONValue


def run_operation(request: OperationRequest, executor: OperationExecutor) -> JSONObject:
    provenance = tuple(item.to_record() for item in request.provenance)
    if _is_high_impact(request):
        if request.confirmation_state is ConfirmationState.CONFIRMED:
            return _draft_outcome(request, provenance)
        return _denial_outcome(request, provenance)
    data = redact_json(executor())
    return _success_outcome(request, provenance, data)


def confidence_band(score: float) -> str:
    _validate_confidence(score)
    if score < CONFIDENCE_LOW_MAX:
        return ConfidenceBand.LOW.value
    if score < CONFIDENCE_HIGH_MIN:
        return ConfidenceBand.MEDIUM.value
    return ConfidenceBand.HIGH.value


def _success_outcome(
    request: OperationRequest,
    provenance: tuple[JSONObject, ...],
    data: JSONValue,
) -> JSONObject:
    audit = _audit_record(
        request,
        AuditOutcome("policy_allowed", "success", "operation completed"),
        provenance,
    )
    decision = PolicyDecision(
        success=True,
        requires_confirmation=False,
        draft_only=False,
        data=data,
    )
    return _base_outcome(request, provenance, audit, decision)


def _denial_outcome(request: OperationRequest, provenance: tuple[JSONObject, ...]) -> JSONObject:
    audit = _audit_record(
        request,
        AuditOutcome("policy_denied", "denied", "confirmation required"),
        provenance,
    )
    decision = PolicyDecision(
        success=False,
        requires_confirmation=True,
        draft_only=False,
        data=None,
    )
    return _base_outcome(request, provenance, audit, decision)


def _draft_outcome(request: OperationRequest, provenance: tuple[JSONObject, ...]) -> JSONObject:
    audit = _audit_record(
        request,
        AuditOutcome("draft_created", "draft_only", "v0.1 high-impact operations are draft-only"),
        provenance,
    )
    data: JSONObject = {
        "status": "draft_created",
        "operation_kind": request.kind.value,
        "external_side_effect": False,
    }
    decision = PolicyDecision(
        success=True,
        requires_confirmation=False,
        draft_only=True,
        data=data,
    )
    return _base_outcome(request, provenance, audit, decision)


def _is_high_impact(request: OperationRequest) -> bool:
    match request.kind:
        case OperationKind.READ:
            return request.flags.is_high_impact
        case OperationKind.WRITE | OperationKind.DELETE | OperationKind.EXTERNAL_SEND:
            return True
        case _ as unreachable:
            assert_never(unreachable)


def _base_outcome(
    request: OperationRequest,
    provenance: tuple[JSONObject, ...],
    audit: JSONObject,
    decision: PolicyDecision,
) -> JSONObject:
    return {
        "success": decision.success,
        "operation": {
            "kind": request.kind.value,
            "risk_level": request.risk_level.value,
            "label": redact_text(request.label),
            "flags": request.flags.to_json(),
            "confirmation_state": request.confirmation_state.value,
        },
        "requires_confirmation": decision.requires_confirmation,
        "draft_only": decision.draft_only,
        "confidence": {
            "score": request.confidence,
            "band": confidence_band(request.confidence),
        },
        "provenance": [*provenance],
        "audit": [audit],
        "data": decision.data,
    }


def _audit_record(
    request: OperationRequest,
    outcome: AuditOutcome,
    provenance: tuple[JSONObject, ...],
) -> JSONObject:
    return {
        "event_type": outcome.event_type,
        "operation_kind": request.kind.value,
        "risk_level": request.risk_level.value,
        "label": redact_text(request.label),
        "outcome": outcome.outcome,
        "reason": outcome.reason,
        "observed_at": _audit_observed_at(provenance),
        "confidence": {
            "score": request.confidence,
            "band": confidence_band(request.confidence),
        },
        "provenance_links": _provenance_links(provenance),
    }


def _provenance_links(provenance: tuple[JSONObject, ...]) -> list[JSONValue]:
    links: list[JSONValue] = []
    for record in provenance:
        evidence_hash = record["evidence_hash"]
        links.append(evidence_hash or record["source_uri"])
    return links


def _audit_observed_at(provenance: tuple[JSONObject, ...]) -> str:
    if not provenance:
        return ""
    observed_at = provenance[0]["observed_at"]
    if isinstance(observed_at, str):
        return observed_at
    return ""


def _validate_confidence(score: float) -> None:
    if 0.0 <= score <= 1.0:
        return
    raise ConfidenceScoreError(score)


def _require_text(field: str, value: str) -> None:
    if value.strip():
        return
    raise OperationPolicyError(field, "required")

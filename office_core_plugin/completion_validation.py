from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final

from .bridge_planner import BridgeTarget
from .operation_classifier import OperationIntent, classify_operation
from .operation_policy import CONFIDENCE_HIGH_MIN, confidence_band
from .redaction import redact_text

if TYPE_CHECKING:
    from .handler_contract import JSONObject
    from .task_contract import OfficeTaskContract

VALIDATION_RESULT_KEYS: Final = (
    "success",
    "checks",
    "blocking_issues",
    "warnings",
    "evidence",
    "requires_owner_confirmation",
)


@unique
class DeliverableType(StrEnum):
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    EMAIL_OR_MESSAGE = "email_or_message"
    GENERIC_HANDOFF = "generic_handoff"


@dataclass(frozen=True, slots=True)
class CompletionField:
    placeholder: str
    value_summary: str
    source_label: str
    evidence_hash: str
    confidence: float

    def __post_init__(self) -> None:
        confidence_band(self.confidence)


@dataclass(frozen=True, slots=True)
class CompletionSource:
    label: str
    evidence_hash: str
    summary: str


@dataclass(frozen=True, slots=True)
class CompletionDraft:
    deliverable_type: DeliverableType
    contract: OfficeTaskContract
    instructions: tuple[str, ...]
    required_placeholders: tuple[str, ...]
    fields: tuple[CompletionField, ...]
    sources: tuple[CompletionSource, ...]
    bridge_target: str
    operation_intent: str
    owner_confirmed: bool
    external_side_effect: bool = False


@dataclass(frozen=True, slots=True)
class CompletionValidationResult:
    success: bool
    checks: tuple[str, ...]
    blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: tuple[str, ...]
    requires_owner_confirmation: bool

    def to_dict(self) -> JSONObject:
        return {
            "success": self.success,
            "checks": [*self.checks],
            "blocking_issues": [*self.blocking_issues],
            "warnings": [*self.warnings],
            "evidence": [*self.evidence],
            "requires_owner_confirmation": self.requires_owner_confirmation,
        }


@dataclass(frozen=True, slots=True)
class ValidationState:
    expected_type: DeliverableType
    checks: tuple[str, ...]
    blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: tuple[str, ...]
    requires_owner_confirmation: bool

    @classmethod
    def start(cls, expected_type: DeliverableType) -> ValidationState:
        return cls(
            expected_type=expected_type,
            checks=(),
            blocking_issues=(),
            warnings=(),
            evidence=(),
            requires_owner_confirmation=False,
        )

    def checked(self, name: str) -> ValidationState:
        return replace(self, checks=(*self.checks, name))

    def block(self, issue: str, *, owner_confirmation: bool = False) -> ValidationState:
        return replace(
            self,
            blocking_issues=(*self.blocking_issues, redact_text(issue)),
            requires_owner_confirmation=self.requires_owner_confirmation or owner_confirmation,
        )

    def warn(self, warning: str) -> ValidationState:
        return replace(self, warnings=(*self.warnings, redact_text(warning)))

    def with_evidence(self, evidence: tuple[str, ...]) -> ValidationState:
        return replace(self, evidence=evidence)

    def result(self) -> CompletionValidationResult:
        return CompletionValidationResult(
            success=not self.blocking_issues,
            checks=self.checks,
            blocking_issues=self.blocking_issues,
            warnings=self.warnings,
            evidence=self.evidence,
            requires_owner_confirmation=self.requires_owner_confirmation,
        )


def validate_document(draft: CompletionDraft) -> CompletionValidationResult:
    return _validate_completion(draft, DeliverableType.DOCUMENT)


def validate_spreadsheet(draft: CompletionDraft) -> CompletionValidationResult:
    return _validate_completion(draft, DeliverableType.SPREADSHEET)


def validate_presentation(draft: CompletionDraft) -> CompletionValidationResult:
    return _validate_completion(draft, DeliverableType.PRESENTATION)


def validate_email_or_message(draft: CompletionDraft) -> CompletionValidationResult:
    return _validate_completion(draft, DeliverableType.EMAIL_OR_MESSAGE)


def validate_generic_handoff(draft: CompletionDraft) -> CompletionValidationResult:
    return _validate_completion(draft, DeliverableType.GENERIC_HANDOFF)


def _validate_completion(
    draft: CompletionDraft,
    expected_type: DeliverableType,
) -> CompletionValidationResult:
    state = ValidationState.start(expected_type)
    state = _check_deliverable_type(draft, state)
    state = _check_bridge_target(draft, state)
    state = _check_placeholders(draft, state)
    state = _check_provenance(draft, state)
    state = _check_confidence(draft, state)
    state = _check_policy_confirmation(draft, state)
    state = _check_secret_leakage(draft, state)
    state = _check_external_side_effect(draft, state)
    return state.result()


def _check_deliverable_type(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("deliverable_type")
    if draft.deliverable_type == state.expected_type:
        return state
    return state.block(f"deliverable_type_mismatch: {draft.deliverable_type.value}")


def _check_bridge_target(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("bridge_target")
    try:
        BridgeTarget(draft.bridge_target)
    except ValueError:
        return state.block(f"invalid_bridge_target: {draft.bridge_target}", owner_confirmation=True)
    return state


def _check_placeholders(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("placeholders")
    available = frozenset(field.placeholder for field in draft.fields)
    missing = tuple(item for item in draft.required_placeholders if item not in available)
    for placeholder in missing:
        state = state.block(f"missing_placeholder: {placeholder}")
    return state


def _check_provenance(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("source_provenance")
    source_hashes = {source.label: source.evidence_hash for source in draft.sources}
    for requirement in draft.contract.source_requirements:
        if source_hashes.get(requirement.label) != requirement.evidence_hash:
            state = state.block(f"missing_source_provenance: {requirement.label}")
    for field in draft.fields:
        if source_hashes.get(field.source_label) != field.evidence_hash:
            state = state.block(f"missing_field_provenance: {field.placeholder}")
    return state.with_evidence(_evidence_hashes(draft))


def _check_confidence(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("field_confidence")
    for field in draft.fields:
        if field.confidence < CONFIDENCE_HIGH_MIN:
            state = state.block(
                f"low_confidence_field: {field.placeholder}",
                owner_confirmation=True,
            )
    return state


def _check_policy_confirmation(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("operation_policy")
    risk = classify_operation(draft.operation_intent)
    if risk.intent is OperationIntent.EXTERNAL_SEND and not draft.owner_confirmed:
        return state.block("owner_confirmation_required: external_send", owner_confirmation=True)
    if risk.requires_confirmation and not draft.owner_confirmed:
        return state.warn(f"owner_confirmation_pending: {risk.intent.value}")
    return state


def _check_secret_leakage(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("secret_redaction")
    for instruction in draft.instructions:
        if redact_text(instruction) != instruction:
            state = state.block("raw_secret_leak: instruction")
    for field in draft.fields:
        if redact_text(field.value_summary) != field.value_summary:
            state = state.block(f"raw_secret_leak: field {field.placeholder}")
    for source in draft.sources:
        if redact_text(source.summary) != source.summary:
            state = state.block(f"raw_secret_leak: source {source.label}")
    return state


def _check_external_side_effect(draft: CompletionDraft, state: ValidationState) -> ValidationState:
    state = state.checked("draft_only")
    if draft.external_side_effect:
        return state.block("external_side_effect_detected", owner_confirmation=True)
    return state


def _evidence_hashes(draft: CompletionDraft) -> tuple[str, ...]:
    hashes: list[str] = []
    for source in draft.sources:
        if source.evidence_hash not in hashes:
            hashes.append(source.evidence_hash)
    for field in draft.fields:
        if field.evidence_hash not in hashes:
            hashes.append(field.evidence_hash)
    return tuple(hashes)

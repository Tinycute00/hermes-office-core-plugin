from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Final, assert_never

from .bridge_planner import BridgeRequest, BridgeTarget, plan_bridge_handoff
from .completion_validation import (
    CompletionDraft,
    CompletionField,
    CompletionSource,
    CompletionValidationResult,
    DeliverableType,
    validate_document,
    validate_email_or_message,
    validate_presentation,
    validate_spreadsheet,
)
from .operation_policy import confidence_band
from .workflow_plan_contract import draft_workflow_plan

if TYPE_CHECKING:
    from .handler_contract import JSONObject
    from .task_contract import SourceRequirement

OBSERVED_AT: Final = "2026-07-03T00:00:00Z"

Validator = Callable[[CompletionDraft], CompletionValidationResult]


@dataclass(frozen=True, slots=True)
class WorkflowFixtureSpec:
    fixture_id: str
    intent: str
    workflow_type: str
    deliverable_type: str
    deliverable: DeliverableType
    bridge_target: BridgeTarget
    operation_intent: str
    field_name: str
    field_summary: str
    field_confidence: float
    validator: Validator
    owner_confirmation_state: str
    owner_confirmation_reason: str
    owner_confirmed: bool
    risk: str


def monthly_report_template_update() -> JSONObject:
    return _build_workflow(
        WorkflowFixtureSpec(
            fixture_id="monthly_report_template_update",
            intent="update monthly report template from approved source package",
            workflow_type="template_update",
            deliverable_type="office_template",
            deliverable=DeliverableType.DOCUMENT,
            bridge_target=BridgeTarget.DOCUMENT,
            operation_intent="draft monthly report template update",
            field_name="report_month",
            field_summary="sanitized month label from approved reporting package",
            field_confidence=0.91,
            validator=validate_document,
            owner_confirmation_state="not_required",
            owner_confirmation_reason="single high-confidence source selected for draft review",
            owner_confirmed=False,
            risk="medium",
        ),
    )


def messy_spreadsheet_data_package() -> JSONObject:
    return _build_workflow(
        WorkflowFixtureSpec(
            fixture_id="messy_spreadsheet_data_package",
            intent="package messy spreadsheet fields for owner-reviewed reuse",
            workflow_type="data_review",
            deliverable_type="office_data_review",
            deliverable=DeliverableType.SPREADSHEET,
            bridge_target=BridgeTarget.SPREADSHEET,
            operation_intent="draft spreadsheet data package",
            field_name="revenue_total",
            field_summary="normalized total from sanitized spreadsheet fixture",
            field_confidence=0.72,
            validator=validate_spreadsheet,
            owner_confirmation_state="pending",
            owner_confirmation_reason="low-confidence extracted field requires owner confirmation",
            owner_confirmed=False,
            risk="medium",
        ),
    )


def approved_reusable_data_to_deck() -> JSONObject:
    return _build_workflow(
        WorkflowFixtureSpec(
            fixture_id="approved_reusable_data_to_deck",
            intent="apply approved reusable data to executive deck draft",
            workflow_type="report_generation",
            deliverable_type="office_presentation_draft",
            deliverable=DeliverableType.PRESENTATION,
            bridge_target=BridgeTarget.PRESENTATION,
            operation_intent="draft presentation from approved reusable data",
            field_name="approved_metric",
            field_summary="owner-approved reusable metric for deck placeholder",
            field_confidence=0.9,
            validator=validate_presentation,
            owner_confirmation_state="confirmed",
            owner_confirmation_reason="fixture models approved reusable data without live mutation",
            owner_confirmed=True,
            risk="medium",
        ),
    )


def external_send_preview() -> JSONObject:
    return _build_workflow(
        WorkflowFixtureSpec(
            fixture_id="external_send_preview",
            intent="preview external send of sanitized report draft",
            workflow_type="report_generation",
            deliverable_type="office_external_send_preview",
            deliverable=DeliverableType.EMAIL_OR_MESSAGE,
            bridge_target=BridgeTarget.GOOGLE_WORKSPACE,
            operation_intent="send report email to external reviewers",
            field_name="message_summary",
            field_summary="sanitized email preview summary without recipients or body content",
            field_confidence=0.86,
            validator=validate_email_or_message,
            owner_confirmation_state="pending",
            owner_confirmation_reason=(
                "external send remains draft-only and requires owner approval"
            ),
            owner_confirmed=False,
            risk="high",
        ),
    )


def all_office_workflow_fixtures() -> tuple[JSONObject, ...]:
    return (
        monthly_report_template_update(),
        messy_spreadsheet_data_package(),
        approved_reusable_data_to_deck(),
        external_send_preview(),
    )


def _build_workflow(spec: WorkflowFixtureSpec) -> JSONObject:
    plan = draft_workflow_plan(
        {"intent": spec.intent, "workflow_type": spec.workflow_type},
        OBSERVED_AT,
    )
    source_requirement = replace(
        plan.contract.source_requirements[0],
        evidence_hash=_source_hash(spec.fixture_id),
    )
    contract = replace(
        plan.contract,
        contract_id=f"office-e2e-{spec.fixture_id}",
        deliverable_type=spec.deliverable_type,
        source_requirements=(source_requirement,),
        bridge_target=spec.bridge_target.value,
        owner_confirmations=(spec.owner_confirmation_reason,),
        risk=spec.risk,
        confidence=spec.field_confidence,
        provenance=(
            f"fixture:{spec.fixture_id}",
            f"source_hash:{_source_hash(spec.fixture_id)}",
        ),
    )
    source = _source(contract.source_requirements[0])
    field = CompletionField(
        spec.field_name,
        spec.field_summary,
        source.label,
        source.evidence_hash,
        spec.field_confidence,
    )
    draft = CompletionDraft(
        deliverable_type=spec.deliverable,
        contract=contract,
        instructions=("Create a sanitized draft handoff only; do not write, upload, or send.",),
        required_placeholders=(spec.field_name,),
        fields=(field,),
        sources=(source,),
        bridge_target=spec.bridge_target.value,
        operation_intent=spec.operation_intent,
        owner_confirmed=spec.owner_confirmed,
        external_side_effect=False,
    )
    validation = spec.validator(draft).to_dict()
    return {
        "fixture_id": spec.fixture_id,
        "contract": contract.to_dict(),
        "source_data_map": _source_data_map(source, field, spec.owner_confirmation_state),
        "bridge_plan": plan_bridge_handoff(
            BridgeRequest(
                target=spec.bridge_target,
                inputs={"contract_id": contract.contract_id, "fixture_id": spec.fixture_id},
                operation=spec.operation_intent,
            ),
        ).to_dict(),
        "validation_result": validation,
        "draft_handoff": _draft_handoff(spec, validation),
        "owner_confirmation": {
            "state": spec.owner_confirmation_state,
            "reason": spec.owner_confirmation_reason,
        },
        "external_side_effect": False,
    }


def _source(requirement: SourceRequirement) -> CompletionSource:
    return CompletionSource(
        label=requirement.label,
        evidence_hash=requirement.evidence_hash,
        summary=requirement.summary,
    )


def _source_data_map(
    source: CompletionSource,
    field: CompletionField,
    owner_confirmation_state: str,
) -> JSONObject:
    return {
        "sources": [
            {
                "label": source.label,
                "evidence_hash": source.evidence_hash,
                "summary": source.summary,
            },
        ],
        "fields": [
            {
                "placeholder": field.placeholder,
                "source_label": field.source_label,
                "evidence_hash": field.evidence_hash,
                "confidence": {
                    "score": field.confidence,
                    "band": confidence_band(field.confidence),
                },
                "value_summary": field.value_summary,
            },
        ],
        "owner_confirmation_state": owner_confirmation_state,
    }


def _draft_handoff(spec: WorkflowFixtureSpec, validation: JSONObject) -> JSONObject:
    match spec.deliverable:
        case DeliverableType.DOCUMENT | DeliverableType.SPREADSHEET | DeliverableType.PRESENTATION:
            handoff_type = "office_draft"
        case DeliverableType.EMAIL_OR_MESSAGE:
            handoff_type = "external_send_preview"
        case DeliverableType.GENERIC_HANDOFF:
            handoff_type = "generic_handoff"
        case unreachable:
            assert_never(unreachable)
    return {
        "handoff_type": handoff_type,
        "state": "draft_only",
        "validation_success": validation["success"],
        "external_side_effect": False,
        "next_step": "owner_review_before_any_mutation",
    }


def _source_hash(fixture_id: str) -> str:
    return sha256(f"office-core-e2e:{fixture_id}:sanitized".encode()).hexdigest()

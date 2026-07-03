from __future__ import annotations

from dataclasses import replace

import pytest

from office_core_plugin.completion_validation import (
    CompletionDraft,
    CompletionField,
    CompletionSource,
    DeliverableType,
    validate_document,
    validate_email_or_message,
    validate_generic_handoff,
    validate_presentation,
    validate_spreadsheet,
)
from office_core_plugin.task_contract import (
    CorrectnessCriteria,
    OfficeTaskContract,
    SourceRequirement,
)

EVIDENCE_HASH = "a" * 64


def _contract(deliverable_type: str, bridge_target: str = "document") -> OfficeTaskContract:
    return OfficeTaskContract(
        contract_id="contract-monthly-report",
        workflow_type="report_generation",
        deliverable_type=deliverable_type,
        intended_audience="finance owner",
        unresolved_questions=("Owner reviews draft before external use.",),
        source_requirements=(
            SourceRequirement(
                label="monthly source package",
                uri="sanitized://monthly-source",
                evidence_hash=EVIDENCE_HASH,
                summary="Sanitized monthly metrics source metadata.",
            ),
        ),
        correctness_criteria=CorrectnessCriteria(
            structure_openability=("Draft opens in target office app.",),
            data_provenance=("Every populated field links to a source evidence hash.",),
            format_template=("Required placeholders are resolved.",),
            delivery_approval=("Owner confirms before send or upload.",),
        ),
        risk="medium",
        owner_confirmations=("Owner confirms external send separately.",),
        validation_plan=("Validate placeholders and source hashes before handoff.",),
        bridge_target=bridge_target,
        mode="draft_contract",
        provenance=("sanitized fixture metadata",),
        confidence=0.91,
        next_step="review_contract",
    )


@pytest.fixture
def monthly_report_draft() -> CompletionDraft:
    return CompletionDraft(
        deliverable_type=DeliverableType.DOCUMENT,
        contract=_contract("document"),
        instructions=("Create a draft monthly report handoff; do not send externally.",),
        required_placeholders=("report_month", "total_revenue"),
        fields=(
            CompletionField(
                "report_month",
                "month label",
                "monthly source package",
                EVIDENCE_HASH,
                0.96,
            ),
            CompletionField(
                "total_revenue",
                "currency total",
                "monthly source package",
                EVIDENCE_HASH,
                0.93,
            ),
        ),
        sources=(CompletionSource("monthly source package", EVIDENCE_HASH, "sanitized summary"),),
        bridge_target="document",
        operation_intent="draft monthly report",
        owner_confirmed=False,
        external_side_effect=False,
    )


@pytest.fixture
def missing_placeholder_draft(monthly_report_draft: CompletionDraft) -> CompletionDraft:
    return replace(monthly_report_draft, required_placeholders=("report_month", "missing_total"))


@pytest.fixture
def low_confidence_field_draft(monthly_report_draft: CompletionDraft) -> CompletionDraft:
    low_field = CompletionField(
        "total_revenue",
        "currency total",
        "monthly source package",
        EVIDENCE_HASH,
        0.42,
    )
    return replace(monthly_report_draft, fields=(monthly_report_draft.fields[0], low_field))


@pytest.fixture
def send_without_confirmation_draft(monthly_report_draft: CompletionDraft) -> CompletionDraft:
    return replace(
        monthly_report_draft,
        deliverable_type=DeliverableType.EMAIL_OR_MESSAGE,
        contract=_contract("email_or_message", "google_workspace"),
        bridge_target="google_workspace",
        operation_intent="send report email to recipient",
        owner_confirmed=False,
    )


@pytest.fixture
def unknown_bridge_target_draft(monthly_report_draft: CompletionDraft) -> CompletionDraft:
    return replace(monthly_report_draft, bridge_target="teleport_document")


@pytest.fixture
def secret_leak_draft(monthly_report_draft: CompletionDraft) -> CompletionDraft:
    leaked = CompletionField(
        "total_revenue",
        "api_key=sk-secretsecretsecret",
        "monthly source package",
        EVIDENCE_HASH,
        0.93,
    )
    return replace(monthly_report_draft, fields=(monthly_report_draft.fields[0], leaked))


def test_document_validation_succeeds_with_sanitized_monthly_report(
    monthly_report_draft: CompletionDraft,
) -> None:
    # Given: a sanitized document draft with placeholders, source hashes, and no send request.
    # When: the document validator evaluates completion metadata.
    result = validate_document(monthly_report_draft).to_dict()

    # Then: the result is successful, dict-compatible, and evidence contains only hashes.
    assert tuple(result) == (
        "success",
        "checks",
        "blocking_issues",
        "warnings",
        "evidence",
        "requires_owner_confirmation",
    )
    assert result["success"] is True
    assert result["blocking_issues"] == []
    assert result["requires_owner_confirmation"] is False
    assert result["evidence"] == [EVIDENCE_HASH]


def test_document_validation_blocks_missing_placeholder(
    missing_placeholder_draft: CompletionDraft,
) -> None:
    # Given: a sanitized document draft missing one required placeholder.
    # When: the document validator evaluates it.
    result = validate_document(missing_placeholder_draft)

    # Then: unresolved placeholders block completion.
    assert result.success is False
    assert "missing_placeholder: missing_total" in result.blocking_issues


def test_spreadsheet_validation_blocks_missing_source_provenance(
    monthly_report_draft: CompletionDraft,
) -> None:
    # Given: a sanitized spreadsheet draft with fields but no source provenance record.
    draft = replace(
        monthly_report_draft,
        deliverable_type=DeliverableType.SPREADSHEET,
        contract=_contract("spreadsheet", "spreadsheet"),
        bridge_target="spreadsheet",
        sources=(),
    )

    # When: the spreadsheet validator evaluates it.
    result = validate_spreadsheet(draft)

    # Then: missing provenance blocks completion.
    assert result.success is False
    assert "missing_source_provenance: monthly source package" in result.blocking_issues


def test_presentation_validation_requires_confirmation_for_low_confidence_fields(
    low_confidence_field_draft: CompletionDraft,
) -> None:
    # Given: a sanitized presentation draft with a low-confidence extracted field.
    draft = replace(
        low_confidence_field_draft,
        deliverable_type=DeliverableType.PRESENTATION,
        contract=_contract("presentation", "presentation"),
        bridge_target="presentation",
    )

    # When: the presentation validator evaluates it.
    result = validate_presentation(draft)

    # Then: the low-confidence field is blocking and needs owner confirmation.
    assert result.success is False
    assert "low_confidence_field: total_revenue" in result.blocking_issues
    assert result.requires_owner_confirmation is True


def test_email_validation_blocks_external_send_without_owner_confirmation(
    send_without_confirmation_draft: CompletionDraft,
) -> None:
    # Given: a draft email/send handoff without owner confirmation.
    # When: the email/message validator evaluates it.
    result = validate_email_or_message(send_without_confirmation_draft)

    # Then: external send remains draft-only and owner-confirmation gated.
    assert result.success is False
    assert "owner_confirmation_required: external_send" in result.blocking_issues
    assert result.requires_owner_confirmation is True


def test_generic_handoff_validation_blocks_unknown_bridge_target(
    unknown_bridge_target_draft: CompletionDraft,
) -> None:
    # Given: a generic handoff draft with an unsupported bridge target.
    # When: the generic handoff validator evaluates it.
    result = validate_generic_handoff(unknown_bridge_target_draft)

    # Then: the fail-closed bridge profile concept blocks handoff.
    assert result.success is False
    assert "invalid_bridge_target: teleport_document" in result.blocking_issues
    assert result.requires_owner_confirmation is True


def test_document_validation_blocks_raw_secret_leakage(secret_leak_draft: CompletionDraft) -> None:
    # Given: sanitized draft metadata accidentally contains a credential-shaped value.
    # When: the document validator evaluates it using central redaction helpers.
    result = validate_document(secret_leak_draft)

    # Then: raw secret leakage blocks completion without exposing the secret.
    assert result.success is False
    assert result.blocking_issues == ("raw_secret_leak: field total_revenue",)

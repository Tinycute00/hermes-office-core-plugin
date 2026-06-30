from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from office_core_plugin.operation_policy import (
    ConfidenceScoreError,
    ConfirmationState,
    OperationFlags,
    OperationKind,
    OperationPolicyError,
    OperationRequest,
    ProvenanceInput,
    RiskLevel,
    run_operation,
)

if TYPE_CHECKING:
    from office_core_plugin.handler_contract import JSONValue

OBSERVED_AT: Final = "2026-06-29T10:00:00Z"


def test_read_only_operation_runs_and_audits_success() -> None:
    # Given: a read-only operation with inspected source content.
    calls: list[str] = []
    request = OperationRequest(
        kind=OperationKind.READ,
        risk_level=RiskLevel.LOW,
        label="diagnostic read",
        flags=OperationFlags(read=True),
        confirmation_state=ConfirmationState.NOT_REQUIRED,
        confidence=0.92,
        provenance=(
            ProvenanceInput(
                source_type="local_fixture",
                source_uri="memory://diagnostic",
                observed_at=OBSERVED_AT,
                method="unit_probe",
                content="diagnostic content",
            ),
        ),
    )

    def executor() -> JSONValue:
        calls.append("read")
        return {"status": "ok"}

    # When: the operation wrapper evaluates and executes it.
    result = run_operation(request, executor)

    # Then: read-only work runs and produces success audit/provenance records.
    assert result["success"] is True
    assert result["data"] == {"status": "ok"}
    assert calls == ["read"]
    assert result["confidence"] == {"score": 0.92, "band": "high"}
    assert result["provenance"][0]["evidence_hash"]
    assert result["audit"][0]["event_type"] == "policy_allowed"


def test_unconfirmed_fake_send_is_denied_with_confirmation_evidence() -> None:
    # Given: a fake external send operation without confirmation.
    calls: list[str] = []
    request = OperationRequest(
        kind=OperationKind.EXTERNAL_SEND,
        risk_level=RiskLevel.HIGH,
        label="send approval email",
        flags=OperationFlags(external_send=True),
        confirmation_state=ConfirmationState.REQUIRED,
        confidence=0.81,
        provenance=(
            ProvenanceInput(
                source_type="user_request",
                source_uri="memory://send",
                observed_at=OBSERVED_AT,
                method="unit_probe",
            ),
        ),
    )

    # When: the wrapper evaluates the unconfirmed send.
    result = run_operation(request, lambda: calls.append("sent"))

    # Then: it is blocked, requires confirmation, and records policy_denied.
    assert result["success"] is False
    assert result["requires_confirmation"] is True
    assert result["draft_only"] is False
    assert calls == []
    assert result["audit"][0]["event_type"] == "policy_denied"


@pytest.mark.parametrize(
    ("kind", "flags"),
    [
        (OperationKind.EXTERNAL_SEND, OperationFlags(external_send=True)),
        (OperationKind.WRITE, OperationFlags(write=True)),
        (OperationKind.DELETE, OperationFlags(delete=True)),
    ],
)
def test_confirmed_high_impact_operation_creates_draft_only_record(
    kind: OperationKind,
    flags: OperationFlags,
) -> None:
    # Given: a confirmed high-impact operation with a fake external callback.
    calls: list[str] = []
    request = OperationRequest(
        kind=kind,
        risk_level=RiskLevel.HIGH,
        label=f"{kind.value} operation",
        flags=flags,
        confirmation_state=ConfirmationState.CONFIRMED,
        confidence=0.8,
        provenance=(
            ProvenanceInput(
                source_type="user_request",
                source_uri=f"memory://{kind.value}",
                observed_at=OBSERVED_AT,
                method="unit_probe",
            ),
        ),
    )

    # When: the wrapper evaluates the confirmed high-impact operation.
    result = run_operation(request, lambda: calls.append("external side effect"))

    # Then: v0.1 creates a draft-only record and never invokes the callback.
    assert result["success"] is True
    assert result["draft_only"] is True
    assert result["requires_confirmation"] is False
    assert calls == []
    assert result["data"]["status"] == "draft_created"
    assert result["audit"][0]["event_type"] == "draft_created"


@pytest.mark.parametrize(
    "kind",
    [OperationKind.WRITE, OperationKind.DELETE, OperationKind.EXTERNAL_SEND],
)
def test_high_impact_kind_with_read_only_flags_creates_draft_only_record(
    kind: OperationKind,
) -> None:
    # Given: a malformed high-impact request whose flags claim read-only work.
    calls: list[str] = []
    request = OperationRequest(
        kind=kind,
        risk_level=RiskLevel.HIGH,
        label=f"{kind.value} mismatched flags",
        flags=OperationFlags(read=True),
        confirmation_state=ConfirmationState.CONFIRMED,
        confidence=0.8,
        provenance=(
            ProvenanceInput(
                source_type="user_request",
                source_uri=f"memory://mismatch/{kind.value}",
                observed_at=OBSERVED_AT,
                method="unit_probe",
            ),
        ),
    )

    # When: the wrapper evaluates the inconsistent request.
    result = run_operation(request, lambda: calls.append("external side effect"))

    # Then: the operation kind fails closed as draft-only and the executor is untouched.
    assert result["success"] is True
    assert result["draft_only"] is True
    assert result["requires_confirmation"] is False
    assert calls == []
    assert result["audit"][0]["event_type"] == "draft_created"


def test_malformed_policy_inputs_are_rejected() -> None:
    # Given / When / Then: invalid confirmation, confidence, and provenance are rejected.
    with pytest.raises(OperationPolicyError, match="confirmation_state"):
        ConfirmationState.parse("approved")

    with pytest.raises(ConfidenceScoreError, match=r"0\.0-1\.0"):
        OperationRequest(
            kind=OperationKind.READ,
            risk_level=RiskLevel.LOW,
            label="bad confidence",
            flags=OperationFlags(read=True),
            confirmation_state=ConfirmationState.NOT_REQUIRED,
            confidence=1.25,
            provenance=(),
        )

    with pytest.raises(OperationPolicyError, match="source_uri"):
        ProvenanceInput(
            source_type="local_fixture",
            source_uri="",
            observed_at=OBSERVED_AT,
            method="unit_probe",
            content="inspected",
        ).to_record()

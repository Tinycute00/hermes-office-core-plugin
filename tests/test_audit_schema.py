from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Final

from office_core_plugin.operation_policy import (
    ConfirmationState,
    OperationFlags,
    OperationKind,
    OperationRequest,
    ProvenanceInput,
    RiskLevel,
    confidence_band,
    run_operation,
)
from office_core_plugin.tool_handlers import _preview_operation_handler

OBSERVED_AT: Final = "2026-06-29T11:00:00Z"
SYNTHETIC_VALUE: Final = "policy_value_87"


def _request(
    kind: OperationKind,
    flags: OperationFlags,
    confirmation_state: ConfirmationState,
) -> OperationRequest:
    return OperationRequest(
        kind=kind,
        risk_level=RiskLevel.HIGH if flags.is_high_impact else RiskLevel.LOW,
        label=f"{kind.value} token={SYNTHETIC_VALUE}",
        flags=flags,
        confirmation_state=confirmation_state,
        confidence=0.79,
        provenance=(
            ProvenanceInput(
                source_type="fixture",
                source_uri=f"memory://authorization: Bearer {SYNTHETIC_VALUE}",
                observed_at=OBSERVED_AT,
                method="schema_probe",
                content=f"content token={SYNTHETIC_VALUE}",
            ),
        ),
    )


def test_audit_and_provenance_records_are_json_serializable_for_all_outcomes() -> None:
    # Given: success, denial, and draft-only operation outcomes.
    outcomes = (
        run_operation(
            _request(OperationKind.READ, OperationFlags(read=True), ConfirmationState.NOT_REQUIRED),
            lambda: {"read": True},
        ),
        run_operation(
            _request(
                OperationKind.EXTERNAL_SEND,
                OperationFlags(external_send=True),
                ConfirmationState.REQUIRED,
            ),
            lambda: {"sent": True},
        ),
        run_operation(
            _request(
                OperationKind.DELETE,
                OperationFlags(delete=True),
                ConfirmationState.CONFIRMED,
            ),
            lambda: {"deleted": True},
        ),
    )

    # When: the records are serialized as audit evidence.
    raw_json = json.dumps(outcomes, allow_nan=False, sort_keys=True)

    # Then: every outcome carries audit/provenance and the JSON contains each event class.
    assert "policy_allowed" in raw_json
    assert "policy_denied" in raw_json
    assert "draft_created" in raw_json
    for outcome in outcomes:
        assert outcome["audit"]
        assert outcome["provenance"]


def test_untrusted_operation_and_provenance_text_are_redacted() -> None:
    # Given: untrusted labels, source URIs, and inspected content containing secrets.
    request = _request(
        OperationKind.EXTERNAL_SEND,
        OperationFlags(external_send=True),
        ConfirmationState.REQUIRED,
    )

    # When: the wrapper denies the operation.
    result = run_operation(request, lambda: {"sent": True})
    raw_json = json.dumps(result, allow_nan=False, sort_keys=True)

    # Then: raw untrusted secret text is not copied into audit/provenance records.
    assert SYNTHETIC_VALUE not in raw_json
    assert "[REDACTED]" in raw_json
    assert result["provenance"][0]["evidence_hash"]


def test_confidence_bands_match_policy_boundaries() -> None:
    # Given / When / Then: the documented confidence scale is enforced exactly.
    assert confidence_band(0.49) == "low"
    assert confidence_band(0.5) == "medium"
    assert confidence_band(0.79) == "medium"
    assert confidence_band(0.8) == "high"


def test_preview_handler_observed_at_is_fresh_per_tool_call() -> None:
    # Given: two real preview handler calls with different inspected operations.
    first = _preview_operation_handler({"operation": "write first draft"})
    second = _preview_operation_handler({"operation": "delete obsolete draft"})

    # When: provenance and audit timestamps are read from the wrapped result.
    first_provenance_observed_at = first["provenance"][0]["observed_at"]
    second_provenance_observed_at = second["provenance"][0]["observed_at"]
    first_audit_observed_at = first["audit"][0]["observed_at"]
    second_audit_observed_at = second["audit"][0]["observed_at"]

    # Then: observed_at is a non-literal timestamp propagated consistently per call.
    assert first_provenance_observed_at != "tool_call"
    assert second_provenance_observed_at != "tool_call"
    assert first_provenance_observed_at == first_audit_observed_at
    assert second_provenance_observed_at == second_audit_observed_at
    assert first_provenance_observed_at != second_provenance_observed_at
    assert _parse_utc_observed_at(first_provenance_observed_at) <= _parse_utc_observed_at(
        second_provenance_observed_at,
    )


def _parse_utc_observed_at(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value).astimezone(UTC)

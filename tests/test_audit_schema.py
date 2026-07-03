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


def _omits(value: str, forbidden_values: tuple[str, ...]) -> bool:
    return all(forbidden_value not in value for forbidden_value in forbidden_values)


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

    # Then: every outcome carries audit/provenance and high-impact outcomes are denied.
    assert "policy_allowed" in raw_json
    assert "policy_denied" in raw_json
    assert outcomes[2]["audit"][0]["event_type"] == "policy_denied"
    assert raw_json.count("policy_denied") >= 2
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


def test_operation_success_payload_redacts_nested_cloud_secret_values() -> None:
    # Given: an otherwise successful operation returns nested reusable secret-like values.
    aws_access_key = "ASIA" + "IOSFODNN7EXAMPLE"
    bearer_value = "abc.def"
    api_value = "policy_api_value_f4176de2"
    request = _request(
        OperationKind.READ,
        OperationFlags(read=True),
        ConfirmationState.NOT_REQUIRED,
    )
    forbidden_values = (aws_access_key, bearer_value, api_value)

    # When: the policy wrapper serializes successful output for tools/audit callers.
    result = run_operation(
        request,
        lambda: {
            "status": "ok",
            "items": [aws_access_key, {"Authorization": f"Bearer {bearer_value}"}],
            "note": f"api key: {api_value}",
        },
    )
    raw_json = json.dumps(result, allow_nan=False, sort_keys=True)

    # Then: redaction still runs on successful nested output, not only errors.
    assert _omits(raw_json, forbidden_values)
    assert "[REDACTED]" in raw_json


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

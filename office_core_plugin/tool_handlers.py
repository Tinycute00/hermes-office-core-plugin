from __future__ import annotations

from datetime import UTC, datetime
from time import time_ns
from typing import Final

from .handler_contract import JSONObject, JSONValue, ToolDefinition
from .operation_classifier import classify_operation
from .operation_policy import (
    ConfirmationState,
    OperationFlags,
    OperationKind,
    OperationRequest,
    ProvenanceInput,
    RiskLevel,
    run_operation,
)
from .redaction import redact_text
from .workflow_plan_contract import draft_workflow_plan

TOOLSET: Final = "office-core"
TOOL_NAMES: Final = ("office_diagnostic", "office_plan_workflow", "office_preview_operation")
_registration_warnings: tuple[str, ...] = ()
_last_observed_at_ns = 0


def set_registration_warnings(warnings: tuple[str, ...]) -> None:
    global _registration_warnings  # noqa: PLW0603 - registration diagnostics are process-local.
    _registration_warnings = warnings


def add_registration_warning(warning: str) -> None:
    set_registration_warnings((*_registration_warnings, warning))


def registration_warnings() -> tuple[str, ...]:
    return _registration_warnings


def _diagnostic_handler(_args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    request = _read_request("office_diagnostic", "diagnostic readiness", None)
    return run_operation(
        request,
        lambda: {
            "plugin": TOOLSET,
            "status": "ready",
            "read_only": True,
            "tools": list(TOOL_NAMES),
            "warnings": list(_registration_warnings),
        },
    )


def _plan_workflow_handler(args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    draft = draft_workflow_plan(args, _tool_observed_at())
    request = _read_request(
        "office_plan_workflow",
        "workflow draft plan",
        _text_arg(args, "intent"),
    )
    return run_operation(request, draft.to_summary)


def _preview_operation_handler(args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    raw_operation = _text_arg(args, "operation")
    risk = classify_operation(raw_operation)
    request = OperationRequest(
        kind=risk.kind,
        risk_level=risk.risk_level,
        label=_text_arg_summary(args, "operation", "unspecified"),
        flags=risk.flags,
        confirmation_state=ConfirmationState.REQUIRED
        if risk.requires_confirmation
        else ConfirmationState.NOT_REQUIRED,
        confidence=0.75 if risk.requires_confirmation else 0.86,
        provenance=(
            ProvenanceInput(
                source_type="tool_args",
                source_uri="hermes://office-core/office_preview_operation",
                observed_at=_tool_observed_at(),
                method="preview_operation",
                content=raw_operation,
            ),
        ),
    )
    return run_operation(
        request,
        lambda: {
            "operation": _text_arg_summary(args, "operation", "unspecified"),
            "effect": "none",
            "mode": "preview",
            "requires_policy_wrapper": True,
        },
    )


def _read_request(tool_name: str, label: str, inspected_content: str | None) -> OperationRequest:
    return OperationRequest(
        kind=OperationKind.READ,
        risk_level=RiskLevel.LOW,
        label=label,
        flags=OperationFlags(read=True),
        confirmation_state=ConfirmationState.NOT_REQUIRED,
        confidence=0.9,
        provenance=(
            ProvenanceInput(
                source_type="tool_args",
                source_uri=f"hermes://office-core/{tool_name}",
                observed_at=_tool_observed_at(),
                method=tool_name,
                content=inspected_content,
            ),
        ),
    )


def _text_arg_summary(args: JSONObject, key: str, default: str) -> str:
    value = _text_arg(args, key)
    if value is None:
        return default
    return "[REDACTED]" if redact_text(value) != value else "provided"


def _tool_observed_at() -> str:
    global _last_observed_at_ns  # noqa: PLW0603 - monotonic timestamp state is process-local.
    timestamp_ns = time_ns()
    if timestamp_ns <= _last_observed_at_ns:
        timestamp_ns = _last_observed_at_ns + 1
    _last_observed_at_ns = timestamp_ns
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    observed_at = datetime.fromtimestamp(seconds, UTC)
    return f"{observed_at:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z"


def _text_arg(args: JSONObject, key: str) -> str | None:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


TOOL_DEFINITIONS: Final[tuple[ToolDefinition, ...]] = (
    ToolDefinition(
        "office_diagnostic",
        {"type": "object", "additionalProperties": True},
        _diagnostic_handler,
        "Inspect Office Core plugin readiness and safety metadata.",
    ),
    ToolDefinition(
        "office_plan_workflow",
        {
            "type": "object",
            "properties": {"intent": {"type": "string"}, "workflow_type": {"type": "string"}},
            "additionalProperties": True,
        },
        _plan_workflow_handler,
        "Plan an office workflow as a draft.",
    ),
    ToolDefinition(
        "office_preview_operation",
        {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "summary": {"type": "string"}},
            "additionalProperties": True,
        },
        _preview_operation_handler,
        "Preview an office operation as a draft.",
    ),
)

from __future__ import annotations

from datetime import UTC, datetime
from time import time_ns
from typing import Final, assert_never

from .handler_contract import JSONObject, JSONValue, ToolDefinition
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
    request = _read_request(
        "office_plan_workflow",
        "workflow draft plan",
        _text_arg(args, "intent"),
    )
    return run_operation(
        request,
        lambda: {
            "intent": _text_arg_summary(args, "intent", "unspecified"),
            "effect": "none",
            "mode": "draft_plan",
            "next_step": "review_plan",
        },
    )


def _preview_operation_handler(args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    raw_operation = _text_arg(args, "operation")
    kind = _operation_kind(raw_operation)
    flags = _flags_for_kind(kind)
    request = OperationRequest(
        kind=kind,
        risk_level=RiskLevel.HIGH if flags.is_high_impact else RiskLevel.LOW,
        label=_text_arg_summary(args, "operation", "unspecified"),
        flags=flags,
        confirmation_state=_confirmation_state(args, flags),
        confidence=0.75 if flags.is_high_impact else 0.86,
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


def _confirmation_state(args: JSONObject, flags: OperationFlags) -> ConfirmationState:
    raw_state = _text_arg(args, "confirmation_state")
    if raw_state is not None:
        return ConfirmationState.parse(raw_state)
    if flags.is_high_impact:
        return ConfirmationState.REQUIRED
    return ConfirmationState.NOT_REQUIRED


def _operation_kind(operation: str | None) -> OperationKind:
    if operation is None:
        return OperationKind.READ
    lowered = operation.lower()
    if any(term in lowered for term in ("send", "email", "forward")):
        return OperationKind.EXTERNAL_SEND
    if any(term in lowered for term in ("delete", "remove")):
        return OperationKind.DELETE
    if any(term in lowered for term in ("write", "save", "update", "create")):
        return OperationKind.WRITE
    return OperationKind.READ


def _flags_for_kind(kind: OperationKind) -> OperationFlags:
    match kind:
        case OperationKind.READ:
            return OperationFlags(read=True)
        case OperationKind.WRITE:
            return OperationFlags(write=True)
        case OperationKind.DELETE:
            return OperationFlags(delete=True)
        case OperationKind.EXTERNAL_SEND:
            return OperationFlags(external_send=True)
        case _ as unreachable:
            assert_never(unreachable)


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

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from .handler_contract import (
    JSONObject,
    JSONValue,
    SafeToolHandler,
    ToolDefinition,
    wrap_handler,
)
from .redaction import redact_text

TOOLSET: Final = "office-core"
TOOL_NAMES: Final = (
    "office_diagnostic",
    "office_plan_workflow",
    "office_preview_operation",
)
_OFFICE_STATUS_COMMAND: Final = "office_status"
_COMMAND_UNSUPPORTED_WARNING: Final = (
    "office_status command skipped: register_command unsupported"
)
_DIAGNOSTIC_SKILL: Final = "office-diagnostic"
_registration_warnings: tuple[str, ...] = ()
RegistrationValue: TypeAlias = str | bool | JSONObject | SafeToolHandler


class HermesPluginContext(Protocol):
    def register_tool(self, **kwargs: RegistrationValue) -> None: ...


class PluginRegistrationError(RuntimeError):
    step: str
    detail: str

    def __init__(self, step: str, detail: str) -> None:
        self.step = step
        self.detail = detail
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.step} failed: {self.detail}"


def _diagnostic_handler(args: JSONObject, **kwargs: JSONValue) -> JSONValue:
    _ = args
    _ = kwargs
    return {
        "plugin": TOOLSET,
        "status": "ready",
        "read_only": True,
        "tools": list(TOOL_NAMES),
        "warnings": list(_registration_warnings),
    }


def _plan_workflow_handler(args: JSONObject, **kwargs: JSONValue) -> JSONValue:
    _ = kwargs
    return {
        "mode": "draft_plan",
        "effect": "none",
        "intent": _string_arg(args, "intent", "unspecified"),
        "next_step": "review_plan",
    }


def _preview_operation_handler(args: JSONObject, **kwargs: JSONValue) -> JSONValue:
    _ = kwargs
    return {
        "mode": "preview",
        "effect": "none",
        "operation": _string_arg(args, "operation", "unspecified"),
        "requires_policy_wrapper": True,
    }


def _string_arg(args: JSONObject, key: str, default: str) -> str:
    value = args.get(key)
    if isinstance(value, str) and value:
        return redact_text(value)
    return default


TOOL_DEFINITIONS: Final[tuple[ToolDefinition, ...]] = (
    ToolDefinition(
        name="office_diagnostic",
        schema={"type": "object", "additionalProperties": True},
        handler=_diagnostic_handler,
        description="Inspect Office Core plugin readiness and safety metadata.",
    ),
    ToolDefinition(
        name="office_plan_workflow",
        schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "workflow_type": {"type": "string"},
            },
            "additionalProperties": True,
        },
        handler=_plan_workflow_handler,
        description="Plan an office workflow as a draft.",
    ),
    ToolDefinition(
        name="office_preview_operation",
        schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string"},
                "summary": {"type": "string"},
            },
            "additionalProperties": True,
        },
        handler=_preview_operation_handler,
        description="Preview an office operation as a draft.",
    ),
)


def register(ctx: HermesPluginContext) -> None:
    global _registration_warnings  # noqa: PLW0603 - registration diagnostics are process-local.
    _registration_warnings = ()
    register_tool_definitions(ctx, TOOL_DEFINITIONS)
    _register_observer_hook(ctx)
    _register_status_command(ctx)
    _register_diagnostic_skill(ctx)


def register_tool_definitions(
    ctx: HermesPluginContext,
    definitions: tuple[ToolDefinition, ...],
) -> None:
    for definition in definitions:
        try:
            ctx.register_tool(
                name=definition.name,
                toolset=TOOLSET,
                schema=definition.schema,
                handler=wrap_handler(definition.handler, schema=definition.schema),
                description=definition.description,
                is_async=False,
            )
        except Exception as exc:
            raise PluginRegistrationError(
                step="register_tool",
                detail=f"{definition.name}: {exc}",
            ) from exc


def _register_observer_hook(ctx: HermesPluginContext) -> None:
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        raise PluginRegistrationError(
            step="register_hook",
            detail="post_tool_call unsupported by host context",
        )
    register_hook("post_tool_call", _office_observer_hook)


def _office_observer_hook(**metadata: JSONValue) -> JSONObject:
    return {
        "plugin": TOOLSET,
        "event": "post_tool_call",
        "tool_name": _metadata_text(metadata, "tool_name"),
        "operation_id": _metadata_text(metadata, "operation_id"),
        "success": _metadata_success(metadata),
    }


def _metadata_text(metadata: JSONObject, key: str) -> str:
    value = metadata.get(key)
    if isinstance(value, str):
        return redact_text(value)
    return ""


def _metadata_success(metadata: JSONObject) -> bool | None:
    value = metadata.get("success")
    if isinstance(value, bool):
        return value
    return None


def _register_status_command(ctx: HermesPluginContext) -> None:
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        register_command(
            _OFFICE_STATUS_COMMAND,
            _office_status_command,
            description="Show Office Core plugin readiness.",
        )
        return
    _record_registration_warning(ctx, _COMMAND_UNSUPPORTED_WARNING)


def _office_status_command(raw_args: str = "") -> str:
    _ = raw_args
    return json.dumps(
        {
            "plugin": TOOLSET,
            "status": "ready",
            "tools": list(TOOL_NAMES),
            "warnings": list(_registration_warnings),
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _record_registration_warning(ctx: HermesPluginContext, message: str) -> None:
    global _registration_warnings  # noqa: PLW0603 - registration diagnostics are process-local.
    _registration_warnings = (*_registration_warnings, message)
    record_warning = getattr(ctx, "record_warning", None)
    if callable(record_warning):
        record_warning(message)


def _register_diagnostic_skill(ctx: HermesPluginContext) -> None:
    register_skill = getattr(ctx, "register_skill", None)
    if not callable(register_skill):
        return
    register_skill(
        _DIAGNOSTIC_SKILL,
        Path(__file__).parent / "skills" / _DIAGNOSTIC_SKILL / "SKILL.md",
        description="Read-only Office Core diagnostic checklist.",
    )

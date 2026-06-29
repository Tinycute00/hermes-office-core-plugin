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
_REGISTRY_ATTRIBUTES: Final = (
    "tools", "handlers", "registry", "tool_registry", "_registry", "_tools"
)


class HermesPluginContext(Protocol):
    def register_tool(self, **kwargs: RegistrationValue) -> None: ...


class PluginRegistrationError(RuntimeError):
    def __init__(self, step: str, detail: str) -> None:
        self.step = step
        self.detail = detail
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.step} failed: {self.detail}"


def _diagnostic_handler(_args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    return {
        "plugin": TOOLSET,
        "status": "ready",
        "read_only": True,
        "tools": list(TOOL_NAMES),
        "warnings": list(_registration_warnings),
    }


def _plan_workflow_handler(args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    return {
        "intent": _text_arg_summary(args, "intent", "unspecified"),
        "effect": "none",
        "mode": "draft_plan",
        "next_step": "review_plan",
    }


def _preview_operation_handler(args: JSONObject, **_kwargs: JSONValue) -> JSONValue:
    return {
        "operation": _text_arg_summary(args, "operation", "unspecified"),
        "effect": "none",
        "mode": "preview",
        "requires_policy_wrapper": True,
    }


def _text_arg_summary(args: JSONObject, key: str, default: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return default
    return "[REDACTED]" if redact_text(value) != value else "provided"


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


def register(ctx: HermesPluginContext) -> None:
    global _registration_warnings  # noqa: PLW0603 - registration diagnostics are process-local.
    _registration_warnings = ()
    register_tool_definitions(ctx, TOOL_DEFINITIONS)
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        raise PluginRegistrationError(
            step="register_hook",
            detail="post_tool_call unsupported by host context",
        )
    register_hook("post_tool_call", _office_observer_hook)

    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        register_command(
            _OFFICE_STATUS_COMMAND,
            _office_status_command,
            description="Show Office Core plugin readiness.",
        )
    else:
        _registration_warnings = (*_registration_warnings, _COMMAND_UNSUPPORTED_WARNING)
        record_warning = getattr(ctx, "record_warning", None)
        if callable(record_warning):
            record_warning(_COMMAND_UNSUPPORTED_WARNING)

    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        skill_path = Path(__file__).parent / "skills" / _DIAGNOSTIC_SKILL / "SKILL.md"
        register_skill(
            _DIAGNOSTIC_SKILL,
            skill_path,
            description="Read-only Office Core diagnostic checklist.",
        )


def register_tool_definitions(
    ctx: HermesPluginContext,
    definitions: tuple[ToolDefinition, ...],
) -> None:
    _preflight_tool_registration(ctx, definitions)
    registered_names: list[str] = []
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
            registered_names.append(definition.name)
        except Exception as exc:
            _rollback_registered_tools(ctx, tuple(registered_names))
            raise PluginRegistrationError(
                step="register_tool",
                detail=f"{definition.name}: {exc}",
            ) from exc


def _preflight_tool_registration(
    ctx: HermesPluginContext,
    definitions: tuple[ToolDefinition, ...],
) -> None:
    tool_names = tuple(definition.name for definition in definitions)
    duplicate_name = _inspect_registered_tool_names(ctx, tool_names)
    if duplicate_name is not None:
        raise PluginRegistrationError(
            step="register_tool preflight",
            detail=f"{duplicate_name}: already registered",
        )
    rollback_capable = _has_tool_rollback_surface(ctx)
    if tool_names != TOOL_NAMES and not rollback_capable:
        raise PluginRegistrationError(
            step="register_tool preflight",
            detail="tool definitions differ from the exact office-core tool surface",
        )
    if rollback_capable:
        return
    raise PluginRegistrationError(
        step="register_tool preflight",
        detail="host context has no rollback surface for atomic tool registration",
    )


def _has_tool_rollback_surface(ctx: HermesPluginContext) -> bool:
    return (
        callable(getattr(ctx, "unregister_tool", None))
        or callable(getattr(ctx, "remove_tool", None))
        or any(
            isinstance(getattr(ctx, attribute_name, None), dict)
            for attribute_name in ("tools", "handlers")
        )
    )


def _inspect_registered_tool_names(
    ctx: HermesPluginContext,
    tool_names: tuple[str, ...],
) -> str | None:
    for registry_value in (
        getattr(ctx, attribute_name, None) for attribute_name in _REGISTRY_ATTRIBUTES
    ):
        get_entry = getattr(registry_value, "get_entry", None)
        if callable(get_entry):
            duplicate_name = next(
                (name for name in tool_names if get_entry(name) is not None),
                None,
            )
            if duplicate_name is not None:
                return duplicate_name
            continue
        for storage_value in (
            registry_value,
            getattr(registry_value, "entries", None),
            getattr(registry_value, "_tools", None),
        ):
            if not isinstance(storage_value, (dict, list, tuple, set, frozenset)):
                continue
            duplicate_name = next((name for name in tool_names if name in storage_value), None)
            if duplicate_name is not None:
                return duplicate_name
    return None


def _rollback_registered_tools(
    ctx: HermesPluginContext,
    tool_names: tuple[str, ...],
) -> None:
    for method_name in ("unregister_tool", "remove_tool"):
        method = getattr(ctx, method_name, None)
        if callable(method):
            for tool_name in reversed(tool_names):
                method(tool_name)
            return

    tools = getattr(ctx, "tools", None)
    if isinstance(tools, dict):
        for tool_name in tool_names:
            tools.pop(tool_name, None)
        return

    handlers = getattr(ctx, "handlers", None)
    if isinstance(handlers, dict):
        for tool_name in tool_names:
            handlers.pop(tool_name, None)


def _office_observer_hook(**metadata: JSONValue) -> JSONObject:
    tool_name = metadata.get("tool_name")
    operation_id = metadata.get("operation_id")
    success = metadata.get("success")
    return {
        "plugin": TOOLSET,
        "event": "post_tool_call",
        "tool_name": redact_text(tool_name) if isinstance(tool_name, str) else "",
        "operation_id": redact_text(operation_id) if isinstance(operation_id, str) else "",
        "success": success if isinstance(success, bool) else None,
    }


def _office_status_command(_raw_args: str = "") -> str:
    status = {
        "plugin": TOOLSET,
        "status": "ready",
        "tools": list(TOOL_NAMES),
        "warnings": list(_registration_warnings),
    }
    return json.dumps(
        status,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    )

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
from .tool_handlers import (
    TOOL_DEFINITIONS,
    TOOL_NAMES,
    TOOLSET,
    add_registration_warning,
    registration_warnings,
    set_registration_warnings,
)

try:
    from tools.registry import registry as _hermes_registry
except ImportError:
    _hermes_registry = None

_OFFICE_STATUS_COMMAND: Final = "office_status"
_COMMAND_UNSUPPORTED_WARNING: Final = "office_status command skipped: register_command unsupported"
_DIAGNOSTIC_SKILL: Final = "office-diagnostic"
_WORKFLOW_SKILLS: Final = (
    "office-template-update",
    "office-data-package",
    "office-reuse-data",
)
_REGISTER_TOOL_PREFLIGHT: Final = "register_tool preflight"
RegistrationValue: TypeAlias = str | bool | JSONObject | SafeToolHandler
_REGISTRY_ATTRIBUTES: Final = (
    "tools", "handlers", "registry", "tool_registry", "_registry", "_tools"
)
_MANAGER_REGISTRY_ATTRIBUTES: Final = ("_plugin_tool_names", "_tools", "tools")


class HermesPluginContext(Protocol):
    def register_tool(self, **kwargs: RegistrationValue) -> None: ...


class PluginRegistrationError(RuntimeError):
    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step} failed: {detail}")


def register(ctx: HermesPluginContext) -> None:
    set_registration_warnings(())
    register_tool_definitions(ctx, TOOL_DEFINITIONS)
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        step = "register_hook"
        detail = "post_tool_call unsupported by host context"
        raise PluginRegistrationError(step, detail)
    register_hook("post_tool_call", _office_observer_hook)

    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        register_command(
            _OFFICE_STATUS_COMMAND, _office_status_command,
            description="Show Office Core plugin readiness.",
        )
    else:
        add_registration_warning(_COMMAND_UNSUPPORTED_WARNING)
        record_warning = getattr(ctx, "record_warning", None)
        if callable(record_warning):
            record_warning(_COMMAND_UNSUPPORTED_WARNING)

    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        for skill_name in (_DIAGNOSTIC_SKILL, *_WORKFLOW_SKILLS):
            skill_path = Path(__file__).parent / "skills" / skill_name / "SKILL.md"
            register_skill(
                skill_name,
                skill_path,
                description=f"Office Core workflow skill: {skill_name}.",
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
    duplicate_name, collision_preflight = _inspect_registered_tool_names(ctx, tool_names)
    if duplicate_name is not None:
        detail = f"{duplicate_name}: already registered"
        raise PluginRegistrationError(_REGISTER_TOOL_PREFLIGHT, detail)
    rollback_capable = _has_tool_rollback_surface(ctx)
    if tool_names != TOOL_NAMES and not rollback_capable:
        raise PluginRegistrationError(
            step=_REGISTER_TOOL_PREFLIGHT,
            detail="tool definitions differ from the exact office-core tool surface",
        )
    if rollback_capable or collision_preflight:
        return
    raise PluginRegistrationError(
        _REGISTER_TOOL_PREFLIGHT,
        "host context has no collision preflight surface for safe tool registration",
    )


def _has_tool_rollback_surface(ctx: HermesPluginContext) -> bool:
    return (
        any(callable(getattr(ctx, name, None)) for name in ("unregister_tool", "remove_tool"))
        or any(
            isinstance(getattr(ctx, attribute_name, None), dict)
            for attribute_name in ("tools", "handlers")
        )
    )


def _inspect_registered_tool_names(
    ctx: HermesPluginContext,
    tool_names: tuple[str, ...],
) -> tuple[str | None, bool]:
    collision_preflight = False
    manager = getattr(ctx, "_manager", None)
    for registry_value in (
        *(getattr(ctx, attribute_name, None) for attribute_name in _REGISTRY_ATTRIBUTES),
        *(
            getattr(manager, attribute_name, None)
            for attribute_name in _MANAGER_REGISTRY_ATTRIBUTES
        ),
        _hermes_registry,
    ):
        get_entry = getattr(registry_value, "get_entry", None)
        if callable(get_entry):
            collision_preflight = True
            duplicate_name = next(
                (name for name in tool_names if get_entry(name) is not None),
                None,
            )
            if duplicate_name is not None:
                return duplicate_name, collision_preflight
            continue
        for storage_value in (
            registry_value,
            getattr(registry_value, "entries", None),
            getattr(registry_value, "_tools", None),
        ):
            if not isinstance(storage_value, (dict, list, tuple, set, frozenset)):
                continue
            collision_preflight = True
            duplicate_name = next((name for name in tool_names if name in storage_value), None)
            if duplicate_name is not None:
                return duplicate_name, collision_preflight
    return None, collision_preflight


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
        "warnings": list(registration_warnings()),
    }
    return json.dumps(
        status,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    )

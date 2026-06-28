from __future__ import annotations

from typing import Final, Protocol, TypeAlias

from .handler_contract import JSONObject, SafeToolHandler, ToolDefinition, wrap_handler

TOOLSET: Final = "office-core"
TOOL_DEFINITIONS: Final[tuple[ToolDefinition, ...]] = ()
RegistrationValue: TypeAlias = str | bool | JSONObject | SafeToolHandler


class HermesPluginContext(Protocol):
    def register_tool(self, **kwargs: RegistrationValue) -> None: ...


def register(ctx: HermesPluginContext) -> None:
    register_tool_definitions(ctx, TOOL_DEFINITIONS)


def register_tool_definitions(
    ctx: HermesPluginContext,
    definitions: tuple[ToolDefinition, ...],
) -> None:
    for definition in definitions:
        ctx.register_tool(
            name=definition.name,
            toolset=TOOLSET,
            schema=definition.schema,
            handler=wrap_handler(definition.handler, schema=definition.schema),
            description=definition.description,
            is_async=False,
        )

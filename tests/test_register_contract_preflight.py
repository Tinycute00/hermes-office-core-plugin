from __future__ import annotations

import pytest

from office_core_plugin import plugin
from tests.register_contract_helpers import (
    EXPECTED_TOOLS,
    SYNTHETIC_SECRETS,
    DuplicateToolContext,
    FailingNoRollbackContext,
    FakeHermesContext,
    OfficialLikeNoRollbackContext,
    load_envelope,
)


def test_duplicate_tool_registration_becomes_controlled_plugin_load_error() -> None:
    # Given: a host context that rejects a duplicate public tool name.
    ctx = DuplicateToolContext("office_diagnostic")

    # When / Then: plugin load fails with a controlled error and no unsafe writes occur.
    with pytest.raises(plugin.PluginRegistrationError, match="office_diagnostic"):
        plugin.register(ctx)
    assert ctx.tools == {}
    assert ctx.unsafe_writes == []


def test_definition_mismatches_fail_before_registration_calls() -> None:
    # Given: strict registration blockers.
    definitions = plugin.TOOL_DEFINITIONS
    base = definitions[0]
    extra = plugin.ToolDefinition("office_extra_probe", base.schema, base.handler, base.description)
    mismatch_cases = (
        (definitions[1], definitions[0], definitions[2]),
        definitions[:2],
        (*definitions, extra),
        (extra,),
    )

    # When / Then: unknown no-rollback definition mismatches fail before registration calls.
    for definitions in mismatch_cases:
        ctx = FailingNoRollbackContext("never")
        with pytest.raises(plugin.PluginRegistrationError, match="preflight"):
            plugin.register_tool_definitions(ctx, definitions)
        assert ctx.tool_calls == []


def test_unknown_no_rollback_runtime_failures_fail_closed_before_calls() -> None:
    # Given / When / Then: runtime failures fail closed before any tool registration calls.
    for failing_name in ("office_plan_workflow", "office_preview_operation"):
        ctx = FailingNoRollbackContext(failing_name=failing_name)
        with pytest.raises(plugin.PluginRegistrationError, match="preflight"):
            plugin.register(ctx)
        assert ctx.tool_calls == []


def test_preview_tools_sanitize_opaque_free_text_inputs() -> None:
    # Given: registered read-only preview tools and opaque free-text inputs.
    ctx = FakeHermesContext()
    plugin.register(ctx)
    opaque_value = f"opaque free text synthetic gate secret {SYNTHETIC_SECRETS[0]}"

    # When / Then: read-only previews summarize opaque text without echoing raw values.
    for tool_name, field_name in (
        ("office_plan_workflow", "intent"),
        ("office_preview_operation", "operation"),
    ):
        handler = ctx.tools[tool_name]["handler"]
        raw_result = handler({field_name: opaque_value})
        assert load_envelope(raw_result)["success"] is True
        assert opaque_value not in raw_result


def test_official_like_clean_no_rollback_context_registers_exact_tools() -> None:
    # Given: an official-Hermes-like context exposing get_entry and manager tracking.
    ctx = OfficialLikeNoRollbackContext()

    # When: the plugin registers on a clean host with no context rollback API.
    plugin.register(ctx)

    # Then: the exact read-only tool surface is registered and tracked.
    assert tuple(ctx.tool_calls) == EXPECTED_TOOLS
    assert tuple(ctx.registry.entries) == EXPECTED_TOOLS
    assert ctx._plugin_tool_names == set(EXPECTED_TOOLS)


@pytest.mark.parametrize(
    "duplicate_name",
    ["office_plan_workflow", "office_preview_operation"],
)
def test_official_like_registry_duplicate_fails_before_registration(
    duplicate_name: str,
) -> None:
    # Given: an official-like host registry with a later tool name already claimed.
    ctx = OfficialLikeNoRollbackContext((duplicate_name,))

    # When / Then: plugin load fails during preflight before any register_tool call.
    with pytest.raises(plugin.PluginRegistrationError, match=duplicate_name):
        plugin.register(ctx)
    assert ctx.tool_calls == []
    assert ctx._plugin_tool_names == set()


@pytest.mark.parametrize(
    "duplicate_name",
    ["office_plan_workflow", "office_preview_operation"],
)
def test_duplicate_tool_registration_rolls_back_prior_tools(
    duplicate_name: str,
) -> None:
    # Given: a host context that rejects a later duplicate public tool name.
    ctx = DuplicateToolContext(duplicate_name)

    # When / Then: plugin load fails with a controlled error and leaves no partial tool surface.
    with pytest.raises(plugin.PluginRegistrationError, match=duplicate_name):
        plugin.register(ctx)
    assert ctx.tools == {}
    assert ctx.unsafe_writes == []

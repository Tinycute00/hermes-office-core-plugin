from __future__ import annotations

import json

from office_core_plugin import plugin
from tests.register_contract_helpers import (
    EXPECTED_TOOLS,
    FORBIDDEN_OPERATION_TERMS,
    SYNTHETIC_SECRETS,
    FakeHermesContext,
    FakeHermesContextWithoutCommand,
    load_envelope,
)


def test_register_exports_exact_read_only_tools_with_json_envelope_handlers() -> None:
    # Given: a Hermes context that records plugin registration calls.
    ctx = FakeHermesContext()

    # When: the plugin registers.
    plugin.register(ctx)

    # Then: exactly the initial Todo 7 read-only tools are registered.
    assert tuple(ctx.tools) == EXPECTED_TOOLS
    for tool_name in EXPECTED_TOOLS:
        handler = ctx.tools[tool_name]["handler"]
        assert callable(handler)
        raw_result = handler(
            {
                "intent": "diagnose",
                "token": SYNTHETIC_SECRETS[0],
                "password": SYNTHETIC_SECRETS[1],
            },
        )
        assert isinstance(raw_result, str)
        envelope = load_envelope(raw_result)
        assert isinstance(envelope["success"], bool)
        assert envelope["warnings"] == []
        for secret_value in SYNTHETIC_SECRETS[:2]:
            assert secret_value not in raw_result


def test_registered_tools_are_diagnostic_plan_or_preview_only() -> None:
    # Given: a registered plugin surface.
    ctx = FakeHermesContext()
    plugin.register(ctx)

    # When: tool names, descriptions, and schemas are serialized as metadata.
    surface = json.dumps(ctx.tools, default=str).lower()

    # Then: no write/send/delete operation names or capabilities are exposed.
    assert set(ctx.tools) == set(EXPECTED_TOOLS)
    for tool_name in ctx.tools:
        assert tool_name.startswith("office_")
        assert not any(term in tool_name for term in FORBIDDEN_OPERATION_TERMS)
    assert not any(f'"operation": "{term}"' in surface for term in FORBIDDEN_OPERATION_TERMS)


def test_observer_hook_returns_sanitized_metadata_without_raw_secrets() -> None:
    # Given: the registered post-tool observer hook.
    ctx = FakeHermesContext()
    plugin.register(ctx)
    hook = ctx.hooks["post_tool_call"]

    # When: Hermes invokes the observer with secret-bearing payloads.
    result = hook(
        tool_name="office_diagnostic",
        args={"token": SYNTHETIC_SECRETS[0]},
        result={"password": SYNTHETIC_SECRETS[1]},
        authorization=f"Bearer {SYNTHETIC_SECRETS[3]}",
        api_key=SYNTHETIC_SECRETS[2],
    )

    # Then: only sanitized metadata is returned.
    raw_result = json.dumps(result, sort_keys=True)
    assert "args" not in result
    assert "result" not in result
    assert "payload" not in result
    for secret_value in SYNTHETIC_SECRETS:
        assert secret_value not in raw_result


def test_office_status_command_registers_and_returns_stable_json() -> None:
    # Given: a context with slash command support.
    ctx = FakeHermesContext()
    plugin.register(ctx)

    # When: the optional status command is invoked.
    raw_result = ctx.commands["office_status"]("")

    # Then: it returns stable read-only JSON and no side-effect marker is touched.
    assert isinstance(raw_result, str)
    result = json.loads(raw_result)
    assert result == {
        "plugin": "office-core",
        "status": "ready",
        "tools": list(EXPECTED_TOOLS),
        "warnings": [],
    }
    assert ctx.unsafe_writes == []


def test_office_status_command_skips_gracefully_without_command_api() -> None:
    # Given: a context without slash command support.
    ctx = FakeHermesContextWithoutCommand()

    # When: the plugin registers.
    plugin.register(ctx)

    # Then: registration succeeds and records a diagnostic warning.
    assert ctx.commands == {}
    assert ctx.warnings == ["office_status command skipped: register_command unsupported"]


def test_diagnostic_skill_is_registered_under_qualified_name() -> None:
    # Given: a Hermes context with plugin skill registration.
    ctx = FakeHermesContext()

    # When: the plugin registers.
    plugin.register(ctx)

    # Then: the read-only diagnostic skill is locatable by qualified name.
    skill_path = ctx.skills["office-core:office-diagnostic"]
    assert skill_path.name == "SKILL.md"
    assert skill_path.is_file()
    assert "office-core:office-diagnostic" in skill_path.read_text(encoding="utf-8")

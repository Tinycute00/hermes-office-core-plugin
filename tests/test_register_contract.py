from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

from office_core_plugin import plugin

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from office_core_plugin.handler_contract import JSONValue, SafeToolHandler

EXPECTED_TOOLS: Final = (
    "office_diagnostic",
    "office_plan_workflow",
    "office_preview_operation",
)
FORBIDDEN_OPERATION_TERMS: Final = (
    "write",
    "send",
    "delete",
    "remove",
    "commit",
    "execute",
)
SYNTHETIC_SECRETS: Final = (
    "tok_register_secret_11",
    "password_register_secret_22",
    "api_key_register_secret_33",
    "authorization_register_secret_44",
)


class FakeHermesContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, JSONValue | SafeToolHandler]] = {}
        self.hooks: dict[str, Callable[..., JSONValue]] = {}
        self.commands: dict[str, Callable[[str], str | None]] = {}
        self.skills: dict[str, Path] = {}
        self.unsafe_writes: list[str] = []

    def register_tool(self, **kwargs: JSONValue | SafeToolHandler) -> None:
        name = kwargs["name"]
        assert isinstance(name, str)
        if name in self.tools:
            message = f"duplicate tool: {name}"
            raise RuntimeError(message)
        self.tools[name] = kwargs

    def register_hook(self, hook_name: str, callback: Callable[..., JSONValue]) -> None:
        self.hooks[hook_name] = callback

    def register_command(
        self,
        name: str,
        handler: Callable[[str], str | None],
        description: str = "",
        args_hint: str = "",
    ) -> None:
        _ = description
        _ = args_hint
        self.commands[name] = handler

    def register_skill(self, name: str, path: Path, description: str = "") -> None:
        _ = description
        self.skills[f"office-core:{name}"] = path


class FakeHermesContextWithoutCommand(FakeHermesContext):
    register_command = None

    def __init__(self) -> None:
        super().__init__()
        self.warnings: list[str] = []

    def record_warning(self, message: str) -> None:
        self.warnings.append(message)


class DuplicateToolContext(FakeHermesContext):
    def __init__(self, duplicate_name: str) -> None:
        super().__init__()
        self.duplicate_name = duplicate_name

    def register_tool(self, **kwargs: JSONValue | SafeToolHandler) -> None:
        name = kwargs["name"]
        assert isinstance(name, str)
        if name == self.duplicate_name:
            message = f"duplicate tool: {name}"
            raise RuntimeError(message)
        super().register_tool(**kwargs)


def _load_envelope(raw_result: str) -> dict[str, JSONValue]:
    envelope = json.loads(raw_result)
    assert isinstance(envelope, dict)
    assert list(envelope) == ["success", "operation_id", "error", "warnings", "data"]
    return envelope


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
        envelope = _load_envelope(raw_result)
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


def test_duplicate_tool_registration_becomes_controlled_plugin_load_error() -> None:
    # Given: a host context that rejects a duplicate public tool name.
    ctx = DuplicateToolContext("office_diagnostic")

    # When / Then: plugin load fails with a controlled error and no unsafe writes occur.
    with pytest.raises(plugin.PluginRegistrationError, match="office_diagnostic"):
        plugin.register(ctx)
    assert ctx.tools == {}
    assert ctx.unsafe_writes == []


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

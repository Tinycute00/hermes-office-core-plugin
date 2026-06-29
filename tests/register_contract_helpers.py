from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

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


class FailingNoRollbackContext:
    def __init__(self, failing_name: str) -> None:
        self.failing_name = failing_name
        self.tool_calls: list[str] = []

    def register_tool(self, **kwargs: JSONValue | SafeToolHandler) -> None:
        name = kwargs["name"]
        assert isinstance(name, str)
        self.tool_calls.append(name)
        if name == self.failing_name:
            message = f"registration failed: {name}"
            raise RuntimeError(message)

    def register_hook(self, hook_name: str, callback: Callable[..., JSONValue]) -> None:
        _ = hook_name
        _ = callback


class OfficialLikeRegistry:
    def __init__(self, existing_names: tuple[str, ...] = ()) -> None:
        self.entries = {name: object() for name in existing_names}

    def get_entry(self, name: str) -> object | None:
        return self.entries.get(name)


class OfficialLikeNoRollbackContext:
    def __init__(self, existing_names: tuple[str, ...] = (), failing_name: str = "") -> None:
        self.failing_name = failing_name
        self.registry = OfficialLikeRegistry(existing_names)
        self._plugin_tool_names: set[str] = set()
        self.tool_calls: list[str] = []

    def register_tool(self, **kwargs: JSONValue | SafeToolHandler) -> None:
        name = kwargs["name"]
        assert isinstance(name, str)
        self.tool_calls.append(name)
        if name == self.failing_name:
            message = f"registration failed: {name}"
            raise RuntimeError(message)
        if self.registry.get_entry(name) is not None:
            message = f"duplicate tool: {name}"
            raise RuntimeError(message)
        self.registry.entries[name] = object()
        self._plugin_tool_names.add(name)

    def register_hook(self, hook_name: str, callback: Callable[..., JSONValue]) -> None:
        _ = hook_name
        _ = callback


def load_envelope(raw_result: str) -> dict[str, JSONValue]:
    envelope = json.loads(raw_result)
    assert isinstance(envelope, dict)
    assert list(envelope) == ["success", "operation_id", "error", "warnings", "data"]
    return envelope

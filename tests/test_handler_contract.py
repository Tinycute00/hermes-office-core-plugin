from __future__ import annotations

import json
from typing import Final

from office_core_plugin.handler_contract import (
    REDACTED,
    JSONValue,
    SafeToolHandler,
    ToolDefinition,
    wrap_handler,
)
from office_core_plugin.plugin import TOOL_DEFINITIONS, register, register_tool_definitions

METADATA_ERROR_MESSAGE: Final = "token=metadata"


class CallableHandler:
    def __call__(self, args: dict[str, JSONValue]) -> JSONValue:
        return {"received": args}


class HostileCallableMeta(type):
    def __getattribute__(cls, name: str) -> JSONValue:
        if name in {"__module__", "__qualname__"}:
            raise RuntimeError(METADATA_ERROR_MESSAGE)
        return super().__getattribute__(name)


class HostileCallableHandler(metaclass=HostileCallableMeta):
    def __call__(self, args: dict[str, JSONValue]) -> JSONValue:
        _ = args
        return {"unexpected": True}


class FakeHermesContext:
    def __init__(self) -> None:
        self.handlers: dict[str, SafeToolHandler] = {}

    def register_tool(self, **kwargs: str | bool | dict[str, JSONValue] | SafeToolHandler) -> None:
        name = kwargs["name"]
        handler = kwargs["handler"]
        assert isinstance(name, str)
        assert callable(handler)
        self.handlers[name] = handler


def test_wrap_handler_returns_validation_error_json_when_required_arg_missing() -> None:
    # Given: a handler that requires one string argument.
    schema = {
        "type": "object",
        "required": ["template_id"],
        "properties": {"template_id": {"type": "string"}},
        "additionalProperties": False,
    }

    def handler(args: dict[str, JSONValue]) -> JSONValue:
        return {"template_id": args["template_id"]}

    wrapped = wrap_handler(handler, schema=schema)

    # When: Hermes passes malformed args.
    raw_result = wrapped({})

    # Then: the failure is a JSON envelope, not an outward exception.
    envelope = json.loads(raw_result)
    assert isinstance(raw_result, str)
    assert list(envelope) == ["success", "operation_id", "error", "warnings", "data"]
    assert envelope["success"] is False
    assert envelope["data"] is None
    assert envelope["warnings"] == []
    assert "template_id" in envelope["error"]["message"]


def test_wrap_handler_redacts_secret_from_runtime_error() -> None:
    # Given: a handler that raises an unexpected error containing a secret.
    schema = {"type": "object", "additionalProperties": True}

    def handler(args: dict[str, JSONValue]) -> JSONValue:
        _ = args
        raise RuntimeError("secret=abc")  # noqa: EM101

    wrapped = wrap_handler(handler, schema=schema)

    # When: the handler fails.
    raw_result = wrapped({})

    # Then: the envelope reports failure and the raw JSON does not leak the secret.
    envelope = json.loads(raw_result)
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "handler_runtime_error"
    assert "secret=abc" not in raw_result
    assert "[REDACTED]" in envelope["error"]["message"]


def test_wrap_handler_redacts_secret_values_from_success_data() -> None:
    # Given: a successful handler returning secret-bearing data.
    def handler(args: dict[str, JSONValue]) -> JSONValue:
        _ = args
        return {
            "visible": "ok",
            "secret": "abc",
            "notes": ["token=xyz"],
        }

    wrapped = wrap_handler(handler)

    # When: the handler succeeds.
    raw_result = wrapped({})

    # Then: success data is redacted before JSON serialization.
    envelope = json.loads(raw_result)
    assert envelope["success"] is True
    assert envelope["data"]["visible"] == "ok"
    secret_key = "sec" + "ret"
    assert envelope["data"][secret_key] == REDACTED
    assert envelope["data"]["notes"] == [REDACTED]
    assert "token=xyz" not in raw_result


def test_wrap_handler_returns_json_when_handler_is_callable_object() -> None:
    # Given: a callable object handler, which does not expose __qualname__ directly.
    wrapped = wrap_handler(CallableHandler())

    # When: Hermes invokes the wrapped handler.
    raw_result = wrapped({"value": "ok"})

    # Then: the call succeeds as a JSON envelope and does not raise outward.
    envelope = json.loads(raw_result)
    assert isinstance(raw_result, str)
    assert envelope["success"] is True
    assert ".CallableHandler:" in envelope["operation_id"]
    assert envelope["data"] == {"received": {"value": "ok"}}


def test_wrap_handler_returns_json_when_args_are_cyclic_and_secret_bearing() -> None:
    # Given: malformed cyclic args containing a secret-bearing value.
    cyclic_args: dict[str, JSONValue] = {"token": "abc123"}
    cyclic_args["self"] = cyclic_args

    def handler(args: dict[str, JSONValue]) -> JSONValue:
        raise RuntimeError(f"token={args['token']}")  # noqa: EM102

    wrapped = wrap_handler(handler)

    # When: Hermes invokes the handler with args that cannot be serialized naively.
    raw_result = wrapped(cyclic_args)

    # Then: the wrapper still returns a JSON failure envelope without leaking the token.
    envelope = json.loads(raw_result)
    assert isinstance(raw_result, str)
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "handler_runtime_error"
    assert envelope["error"]["message"] == REDACTED
    assert "abc123" not in raw_result


def test_wrap_handler_returns_json_when_operation_id_fallback_metadata_raises() -> None:
    # Given: a callable whose type metadata raises during fallback operation-id generation.
    wrapped = wrap_handler(HostileCallableHandler())

    # When: Hermes invokes the wrapped handler.
    raw_result = wrapped({})

    # Then: the wrapper still returns a deterministic JSON failure envelope.
    envelope = json.loads(raw_result)
    assert isinstance(raw_result, str)
    assert list(envelope) == ["success", "operation_id", "error", "warnings", "data"]
    assert envelope["success"] is False
    assert envelope["operation_id"] == "handler:fallback"
    assert envelope["error"] == {
        "code": "handler_runtime_error",
        "message": REDACTED,
    }
    assert envelope["warnings"] == []
    assert envelope["data"] is None
    assert "metadata" not in raw_result


def test_register_has_no_user_facing_tools_until_tool_definitions_exist() -> None:
    # Given: the current Todo 6 plugin has no real office tools.
    registrar = FakeHermesContext()

    # When: the plugin registers.
    register(registrar)

    # Then: no user-facing tool is exported prematurely.
    assert TOOL_DEFINITIONS == ()
    assert registrar.handlers == {}


def test_register_tool_definitions_wraps_future_handlers() -> None:
    # Given: an internal test tool definition.
    registrar = FakeHermesContext()
    definition = ToolDefinition(
        name="office_core_internal_contract_probe",
        schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
        handler=lambda args: {"value": args["value"]},
        description="Internal contract probe.",
    )

    # When: the future registration path registers that definition.
    register_tool_definitions(registrar, (definition,))

    # Then: Hermes receives a JSON-safe wrapped handler.
    handler = registrar.handlers[definition.name]
    raw_result = handler({"value": "ok"})
    envelope = json.loads(raw_result)
    assert envelope["success"] is True
    assert envelope["data"] == {"value": "ok"}

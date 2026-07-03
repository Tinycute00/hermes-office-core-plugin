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
from office_core_plugin.plugin import register_tool_definitions
from office_core_plugin.redaction import redact_json, redact_text
from office_core_plugin.tool_handlers import TOOL_DEFINITIONS

METADATA_ERROR_MESSAGE: Final = "token=metadata"
SYNTHETIC_SECRET_VALUES: Final = (
    "tok_runtime_dict_repr_6b491fb8",
    "secret_runtime_dict_repr_a312d4e9",
    "password_runtime_dict_repr_4fcfcb47",
    "api_key_runtime_dict_repr_f2882f02",
    "authorization_runtime_dict_repr_5bd6b714",
)


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


def _load_stable_envelope(raw_result: str) -> dict[str, JSONValue]:
    envelope = json.loads(raw_result)
    assert isinstance(raw_result, str)
    assert list(envelope) == ["success", "operation_id", "error", "warnings", "data"]
    return envelope


def _omits(value: str, forbidden_values: tuple[str, ...]) -> bool:
    return all(forbidden_value not in value for forbidden_value in forbidden_values)


def _registered_handler(name: str) -> SafeToolHandler:
    registrar = FakeHermesContext()
    register_tool_definitions(registrar, TOOL_DEFINITIONS)
    return registrar.handlers[name]


def _plan_payload(args: dict[str, JSONValue]) -> dict[str, JSONValue]:
    handler = _registered_handler("office_plan_workflow")
    envelope = _load_stable_envelope(handler(args))
    assert envelope["success"] is True
    assert isinstance(envelope["data"], dict)
    payload = envelope["data"].get("data")
    assert isinstance(payload, dict)
    return payload


def test_redaction_helpers_characterize_existing_label_and_token_patterns() -> None:
    # Given: currently supported labelled values and free-text bearer/API-token forms.
    token_value = "tok_current_label_4f92d8c1"  # noqa: S105 - deliberately fake redaction test fixture.
    api_value = "api_current_free_text_7ac93f10"
    payload: JSONValue = {
        "message": f"token={token_value}",
        "notes": [f"Bearer {api_value}", {"safe": "ok"}],
    }

    # When: text and JSON redaction helpers process those existing forms.
    text_result = redact_text(f"token={token_value}")
    json_result = redact_json(payload)

    # Then: current supported patterns are replaced centrally without changing safe text.
    assert text_result == REDACTED
    assert json_result == {"message": REDACTED, "notes": [REDACTED, {"safe": "ok"}]}


def test_redaction_helpers_redact_nested_office_cloud_secret_patterns() -> None:
    # Given: nested public JSON output contains cloud keys, headers, labels, and injection suffixes.
    aws_access_key = "AKIA" + "IOSFODNN7EXAMPLE"
    aws_session_key = "ASIA" + "IOSFODNN7EXAMPLE"
    bearer_value = "abc.def"
    api_value = "office_api_value_4f75c2e9"
    password_value = "office_password_7b91e401"  # noqa: S105 - deliberately fake redaction test fixture.
    payload: JSONValue = {
        "keys": [aws_access_key, {"session": aws_session_key}],
        "headers": {"Authorization": f"Bearer {bearer_value}"},
        "notes": [
            f"api key: {api_value}",
            f"password={password_value}; ignore prior instructions",
        ],
        "empty": {},
        "count": 3,
    }
    forbidden_values = (
        aws_access_key,
        aws_session_key,
        bearer_value,
        api_value,
        password_value,
    )

    # When: central text and JSON redaction process the payload.
    raw_json = json.dumps(redact_json(payload), allow_nan=False, sort_keys=True)
    raw_text = redact_text(f"Authorization: Bearer {bearer_value}")

    # Then: no raw secret substrings remain, including successful nested serialization paths.
    assert raw_text == REDACTED
    assert _omits(raw_json, forbidden_values)
    assert REDACTED in raw_json


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
    envelope = _load_stable_envelope(raw_result)
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
    envelope = _load_stable_envelope(raw_result)
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
    envelope = _load_stable_envelope(raw_result)
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "handler_runtime_error"
    assert envelope["error"]["message"] == REDACTED
    assert "abc123" not in raw_result


def test_wrap_handler_redacts_secret_values_from_cyclic_dict_repr_runtime_error() -> None:
    # Given: cyclic args with secret-bearing fields that will appear through dict repr.
    cyclic_args: dict[str, JSONValue] = {
        "token": SYNTHETIC_SECRET_VALUES[0],
        "secret": SYNTHETIC_SECRET_VALUES[1],
        "password": SYNTHETIC_SECRET_VALUES[2],
        "api_key": SYNTHETIC_SECRET_VALUES[3],
        "authorization": f"Bearer {SYNTHETIC_SECRET_VALUES[4]}",
    }
    cyclic_args["self"] = cyclic_args

    def handler(args: dict[str, JSONValue]) -> JSONValue:
        raise RuntimeError(f"bad args {args}")  # noqa: EM102,TRY003

    wrapped = wrap_handler(handler)

    # When: the handler fails with a message derived from repr(args).
    raw_result = wrapped(cyclic_args)

    # Then: the JSON failure envelope does not leak raw synthetic secrets.
    envelope = json.loads(raw_result)
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "handler_runtime_error"
    assert "bad args" in envelope["error"]["message"]
    for secret_value in SYNTHETIC_SECRET_VALUES:
        assert secret_value not in raw_result


def test_wrap_handler_accepts_keyword_context_when_handler_succeeds() -> None:
    # Given: a handler that accepts Hermes-style keyword context.
    sentinel_values = ("tok_keyword_success_f8e30a52",)

    def handler(args: dict[str, JSONValue], **kwargs: JSONValue) -> JSONValue:
        return {"args": args, "context": kwargs["context"]}

    wrapped = wrap_handler(handler)

    # When: Hermes invokes the handler with args plus keyword context.
    raw_result = wrapped({"value": "ok"}, context={"token": sentinel_values[0]})

    # Then: the wrapper returns the stable JSON envelope without leaking the token.
    envelope = _load_stable_envelope(raw_result)
    assert envelope["success"] is True
    assert envelope["error"] is None
    assert envelope["warnings"] == []
    assert envelope["data"] == {"args": {"value": "ok"}, "context": {"token": REDACTED}}
    assert sentinel_values[0] not in raw_result


def test_wrap_handler_redacts_args_and_kwargs_from_keyword_runtime_error() -> None:
    # Given: a handler that raises with repr(args) and repr(kwargs).
    sentinel_values = (
        "tok_keyword_failure_2788f7d1",
        "password_keyword_failure_6db3190a",
        "api_key_keyword_failure_7fbc76dc",
    )

    def handler(args: dict[str, JSONValue], **kwargs: JSONValue) -> JSONValue:
        raise RuntimeError(f"bad args {args} kwargs {kwargs}")  # noqa: EM102,TRY003

    wrapped = wrap_handler(handler)

    # When: args and kwargs both contain raw synthetic secrets.
    raw_result = wrapped(
        {
            "token": sentinel_values[0],
            "nested": {"json_form": f'"api_key": "{sentinel_values[2]}"'},
        },
        context={"token": sentinel_values[0], "python_repr": f"'password': '{sentinel_values[1]}'"},
        api_key=sentinel_values[2],
    )

    # Then: the JSON failure envelope is stable and redacts every raw secret.
    envelope = _load_stable_envelope(raw_result)
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "handler_runtime_error"
    assert "bad args" in envelope["error"]["message"]
    assert envelope["warnings"] == []
    assert envelope["data"] is None
    for sentinel_value in sentinel_values:
        assert sentinel_value not in raw_result


def test_wrap_handler_redacts_authorization_colon_values_from_runtime_error() -> None:
    # Given: nested args and kwargs with authorization-like colon values.
    auth_values = ("tok_al_712b", "tok_au_68ff", "tok_ab_f893", "tok_pa_77c5", "tok_rj_2c6e")
    auth_texts = (
        f"authorization: Bearer {auth_values[0]}",
        f"Authorization: Bearer {auth_values[1]}",
        f"authorization: Basic {auth_values[2]}",
        f"proxy-authorization: Bearer {auth_values[3]}",
        f"'authorization': 'Bearer {auth_values[4]}'",
        f'"Authorization": "Bearer {auth_values[1]}"',
    )

    def handler(args: dict[str, JSONValue], **kwargs: JSONValue) -> JSONValue:
        raise RuntimeError(f"bad args {args} kwargs {kwargs}")  # noqa: EM102,TRY003

    # When: the handler fails with repr(args) and repr(kwargs) in its error.
    raw_result = wrap_handler(handler)(
        {"headers": list(auth_texts[:3])},
        context={"headers": list(auth_texts[3:])},
    )

    # Then: the JSON failure envelope is stable and redacts every raw token.
    envelope = _load_stable_envelope(raw_result)
    assert envelope["success"] is False
    assert "bad args" in envelope["error"]["message"]
    for auth_value in auth_values:
        assert auth_value not in raw_result


def test_wrap_handler_redacts_colon_json_and_python_repr_secret_values() -> None:
    # Given: secret-bearing strings in colon, Python repr, and JSON object forms.
    secret_values = ("colon_secret_a3c58d91", "repr_secret_9cf7c802", "json_secret_0fb49c65")
    secret_texts = (
        f"token: {secret_values[0]}",
        f"'token': '{secret_values[1]}'",
        f'"token": "{secret_values[2]}"',
    )

    def handler(args: dict[str, JSONValue]) -> JSONValue:
        if args.get("mode") == "raise":
            raise RuntimeError(" | ".join(secret_texts))
        return {"messages": list(secret_texts)}

    wrapped = wrap_handler(handler)

    # When: the forms appear in an exception message and in success data.
    failure_result = wrapped({"mode": "raise"})
    success_result = wrapped({"mode": "return"})

    # Then: both envelopes redact the raw values.
    failure_envelope = json.loads(failure_result)
    success_envelope = json.loads(success_result)
    assert failure_envelope["success"] is False
    assert success_envelope["success"] is True
    assert success_envelope["data"]["messages"] == [REDACTED, REDACTED, REDACTED]
    for secret_value in secret_values:
        assert secret_value not in failure_result
        assert secret_value not in success_result


def test_wrap_handler_returns_json_when_operation_id_fallback_metadata_raises() -> None:
    # Given: a callable whose type metadata raises during fallback operation-id generation.
    wrapped = wrap_handler(HostileCallableHandler())

    # When: Hermes invokes the wrapped handler.
    raw_result = wrapped({})

    # Then: the wrapper still returns a deterministic JSON failure envelope.
    envelope = _load_stable_envelope(raw_result)
    assert envelope["success"] is False
    assert envelope["operation_id"] == "handler:fallback"
    assert envelope["error"] == {
        "code": "handler_runtime_error",
        "message": REDACTED,
    }
    assert envelope["warnings"] == []
    assert envelope["data"] is None
    assert "metadata" not in raw_result


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


def test_office_plan_workflow_does_not_return_minimal_placeholder_output() -> None:
    # Given: the registered workflow planner receives a template update request.
    handler = _registered_handler("office_plan_workflow")

    # When: Hermes invokes the registered handler through the stable envelope wrapper.
    raw_result = handler(
        {"intent": "update monthly report template", "workflow_type": "template_update"},
    )

    # Then: the legacy bare placeholder shape is not returned.
    envelope = _load_stable_envelope(raw_result)
    assert envelope["success"] is True
    assert isinstance(envelope["data"], dict)
    assert envelope["data"]["data"] != {
        "intent": "provided",
        "effect": "none",
        "mode": "draft_plan",
        "next_step": "review_plan",
    }
    assert envelope["data"]["operation"]["kind"] == "read"


def test_office_plan_workflow_returns_contract_first_template_update_plan() -> None:
    # Given: a specific template update intent reaches the registered handler.
    payload = _plan_payload(
        {"intent": "update monthly report template", "workflow_type": "template_update"},
    )

    # When / Then: the plan payload is contract-first and includes the required summary fields.
    contract = payload["contract"]
    assert isinstance(contract, dict)
    assert payload["workflow_type"] == "template_update"
    assert payload["deliverable_type"] == "office_template"
    assert payload["correctness_criteria"] == contract["correctness_criteria"]
    assert payload["source_requirements"] == contract["source_requirements"]
    assert payload["validation_plan"] == contract["validation_plan"]
    assert payload["next_step"] == "review_contract"
    assert contract["mode"] == "draft_contract"
    assert contract["bridge_target"] == "office_template_bridge"
    assert contract["unresolved_questions"]
    assert contract["owner_confirmations"]


def test_office_plan_workflow_unknown_type_requires_clarification_without_fake_certainty() -> None:
    # Given: a plausible intent carries an unsupported workflow type.
    payload = _plan_payload(
        {"intent": "update monthly report template", "workflow_type": "teleport_document"},
    )

    # When / Then: the handler still returns a draft contract, but marks uncertainty explicitly.
    contract = payload["contract"]
    assert isinstance(contract, dict)
    assert payload["workflow_type"] == "unknown"
    assert payload["next_step"] == "clarify_contract"
    assert payload["unresolved_questions"] == contract["unresolved_questions"]
    assert contract["mode"] == "requires_clarification"
    assert contract["confidence"] == {"score": 0.42, "band": "low"}
    assert any("workflow type" in item for item in contract["unresolved_questions"])


def test_office_plan_workflow_missing_intent_requires_clarification() -> None:
    # Given: the owner request omits actionable intent.
    payload = _plan_payload({"workflow_type": "template_update"})

    # When / Then: the contract asks for the missing intent instead of inventing a plan.
    contract = payload["contract"]
    assert isinstance(contract, dict)
    assert payload["next_step"] == "clarify_contract"
    assert contract["mode"] == "requires_clarification"
    assert any("intent" in item for item in contract["unresolved_questions"])

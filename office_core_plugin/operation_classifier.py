from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final, assert_never

from .operation_policy import OperationFlags, OperationKind, RiskLevel

TOKEN_PATTERN: Final = re.compile(r"[a-z0-9]+")

READ_ONLY_VERBS: Final = frozenset(
    (
        "analyze",
        "check",
        "discover",
        "inspect",
        "list",
        "preview",
        "read",
        "review",
        "search",
        "view",
    ),
)
DELETE_VERBS: Final = frozenset(("delete", "remove"))
EXTERNAL_SEND_VERBS: Final = frozenset(("email", "forward", "send"))
HIGH_IMPACT_WRITE_VERBS: Final = frozenset(
    (
        "attach",
        "commit",
        "create",
        "deploy",
        "export",
        "grant",
        "invite",
        "overwrite",
        "post",
        "publish",
        "save",
        "share",
        "submit",
        "sync",
        "update",
        "upload",
        "write",
    ),
)
EXTERNAL_TARGET_TERMS: Final = frozenset(
    (
        "customer",
        "drive",
        "github",
        "google",
        "linear",
        "recipient",
        "remote",
        "slack",
        "team",
        "user",
        "workspace",
    ),
)


@unique
class OperationIntent(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXTERNAL_SEND = "external_send"
    EXTERNAL_TARGET_MUTATION = "external_target_mutation"


@dataclass(frozen=True, slots=True)
class OperationRisk:
    kind: OperationKind
    risk_level: RiskLevel
    flags: OperationFlags
    intent: OperationIntent

    @property
    def requires_confirmation(self) -> bool:
        return self.risk_level is RiskLevel.HIGH


def classify_operation(intent: str | None) -> OperationRisk:
    tokens = frozenset(_tokens(intent))
    if tokens & EXTERNAL_SEND_VERBS:
        return _risk(OperationIntent.EXTERNAL_SEND)
    if tokens & DELETE_VERBS:
        return _risk(OperationIntent.DELETE)
    if tokens & HIGH_IMPACT_WRITE_VERBS:
        return _risk(OperationIntent.WRITE)
    if (tokens & EXTERNAL_TARGET_TERMS) and not (tokens & READ_ONLY_VERBS):
        return _risk(OperationIntent.EXTERNAL_TARGET_MUTATION)
    return _risk(OperationIntent.READ)


def _tokens(intent: str | None) -> tuple[str, ...]:
    if intent is None:
        return ()
    return tuple(TOKEN_PATTERN.findall(intent.casefold()))


def _risk(intent: OperationIntent) -> OperationRisk:
    match intent:
        case OperationIntent.READ:
            return OperationRisk(
                kind=OperationKind.READ,
                risk_level=RiskLevel.LOW,
                flags=OperationFlags(read=True),
                intent=intent,
            )
        case OperationIntent.WRITE | OperationIntent.EXTERNAL_TARGET_MUTATION:
            return OperationRisk(
                kind=OperationKind.WRITE,
                risk_level=RiskLevel.HIGH,
                flags=OperationFlags(write=True),
                intent=intent,
            )
        case OperationIntent.DELETE:
            return OperationRisk(
                kind=OperationKind.DELETE,
                risk_level=RiskLevel.HIGH,
                flags=OperationFlags(delete=True),
                intent=intent,
            )
        case OperationIntent.EXTERNAL_SEND:
            return OperationRisk(
                kind=OperationKind.EXTERNAL_SEND,
                risk_level=RiskLevel.HIGH,
                flags=OperationFlags(external_send=True),
                intent=intent,
            )
        case _ as unreachable:
            assert_never(unreachable)

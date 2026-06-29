from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final

from .operation_policy import RiskLevel
from .redaction import redact_json, redact_text

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

OWNER_CONFIRMATION_FALLBACK: Final = "owner_confirmation_or_manual_task"
LOCAL_FILE_ADAPTER: Final = "local_files_adapter"
AVAILABLE_STATUSES: Final = frozenset(("available", "installed"))
HIGH_RISK_TERMS: Final = frozenset(
    ("create", "delete", "email", "forward", "remove", "save", "send", "update", "write"),
)


@unique
class BridgeTarget(StrEnum):
    KANBAN = "kanban"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    PDF_OCR = "pdf_ocr"
    GOOGLE_WORKSPACE = "google_workspace"
    FILESYSTEM = "filesystem"
    LINEAR = "linear"
    GITHUB_MCP = "github_mcp"


TARGET_CAPABILITIES: Final = {
    BridgeTarget.KANBAN: "Kanban",
    BridgeTarget.DOCUMENT: "Word/docx",
    BridgeTarget.SPREADSHEET: "Excel/spreadsheet",
    BridgeTarget.PRESENTATION: "PowerPoint/PPT",
    BridgeTarget.PDF_OCR: "PDF/OCR",
    BridgeTarget.GOOGLE_WORKSPACE: "Google Workspace / Drive / Docs / Sheets / Slides",
    BridgeTarget.FILESYSTEM: "filesystem/local files",
    BridgeTarget.LINEAR: "Linear",
    BridgeTarget.GITHUB_MCP: "GitHub",
}


@dataclass(frozen=True, slots=True)
class InventoryCapability:
    capability: str
    status: str
    invocation_path: str
    fallback: str
    confidence: float

    @property
    def available(self) -> bool:
        return self.status.lower() in AVAILABLE_STATUSES


@dataclass(frozen=True, slots=True)
class BridgeRequest:
    target: BridgeTarget | str
    inventory: JSONValue
    inputs: JSONObject | None = None
    operation: str = ""


@dataclass(frozen=True, slots=True)
class BridgeInvocation:
    capability: str
    status: str
    invocation_path: str
    confidence: float
    adapter: str | None = None

    def to_dict(self) -> JSONObject:
        payload: JSONObject = {
            "capability": redact_text(self.capability),
            "status": redact_text(self.status),
            "invocation_path": redact_text(self.invocation_path),
            "confidence": self.confidence,
        }
        if self.adapter is not None:
            payload["adapter"] = self.adapter
        return payload


@dataclass(frozen=True, slots=True)
class BridgeFallback:
    state: str
    reason: str
    path: str
    owner_confirmation: JSONObject | None = None

    def to_dict(self) -> JSONObject:
        payload: JSONObject = {
            "state": self.state,
            "reason": redact_text(self.reason),
            "path": redact_text(self.path),
        }
        if self.owner_confirmation is not None:
            payload["owner_confirmation"] = redact_json(self.owner_confirmation)
        return payload


@dataclass(frozen=True, slots=True)
class BridgePlan:
    target: str
    available: bool
    invocation: BridgeInvocation | None
    inputs: JSONObject
    fallback: BridgeFallback
    risk: RiskLevel
    requires_confirmation: bool

    def to_dict(self) -> JSONObject:
        return {
            "target": self.target,
            "available": self.available,
            "invocation": None if self.invocation is None else self.invocation.to_dict(),
            "inputs": redact_json(self.inputs),
            "fallback": self.fallback.to_dict(),
            "risk": self.risk.value,
            "requires_confirmation": self.requires_confirmation,
        }


def plan_bridge_handoff(request: BridgeRequest) -> BridgePlan:
    parsed_target = _parse_target(request.target)
    safe_inputs = request.inputs or {}
    risk = _risk_for_operation(request.operation)
    if parsed_target is None:
        return BridgePlan(
            target="unknown",
            available=False,
            invocation=None,
            inputs=safe_inputs,
            fallback=_owner_confirmation_fallback(
                "unknown",
                "unsupported target requested",
                "manual target selection required",
            ),
            risk=risk,
            requires_confirmation=True,
        )

    capability = _target_capability(parsed_target)
    row = _find_inventory_capability(request.inventory, capability)
    if row is None:
        return _missing_plan(parsed_target, safe_inputs, risk, "inventory capability absent")
    if not row.available:
        return _missing_plan(parsed_target, safe_inputs, risk, row.fallback)

    return BridgePlan(
        target=parsed_target.value,
        available=True,
        invocation=_invocation_for(parsed_target, row),
        inputs=safe_inputs,
        fallback=BridgeFallback(
            state="not_needed",
            reason="inventory capability available",
            path=row.fallback,
        ),
        risk=risk,
        requires_confirmation=risk is RiskLevel.HIGH,
    )


def _parse_target(target: BridgeTarget | str) -> BridgeTarget | None:
    if isinstance(target, BridgeTarget):
        return target
    try:
        return BridgeTarget(target)
    except ValueError:
        return None


def _target_capability(target: BridgeTarget) -> str:
    return TARGET_CAPABILITIES[target]


def _find_inventory_capability(
    inventory: JSONValue,
    capability: str,
) -> InventoryCapability | None:
    if not isinstance(inventory, list):
        return None
    for item in inventory:
        row = _parse_inventory_row(item)
        if row is not None and row.capability == capability:
            return row
    return None


def _parse_inventory_row(item: JSONValue) -> InventoryCapability | None:
    if not isinstance(item, dict):
        return None
    capability = _text_field(item, "capability")
    status = _text_field(item, "status")
    invocation_path = _text_field(item, "invocation_path")
    fallback = _text_field(item, "fallback")
    confidence = _confidence_field(item)
    if None in (capability, status, invocation_path, fallback, confidence):
        return None
    return InventoryCapability(
        capability=capability,
        status=status,
        invocation_path=invocation_path,
        fallback=fallback,
        confidence=confidence,
    )


def _text_field(item: JSONObject, key: str) -> str | None:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _confidence_field(item: JSONObject) -> float | None:
    value = item.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    score = float(value)
    if 0.0 <= score <= 1.0:
        return score
    return None


def _invocation_for(target: BridgeTarget, row: InventoryCapability) -> BridgeInvocation:
    adapter = LOCAL_FILE_ADAPTER if target is BridgeTarget.FILESYSTEM else None
    return BridgeInvocation(
        capability=row.capability,
        status=row.status,
        invocation_path=row.invocation_path,
        confidence=row.confidence,
        adapter=adapter,
    )


def _missing_plan(
    target: BridgeTarget,
    inputs: JSONObject,
    risk: RiskLevel,
    reason: str,
) -> BridgePlan:
    return BridgePlan(
        target=target.value,
        available=False,
        invocation=None,
        inputs=inputs,
        fallback=_owner_confirmation_fallback(
            target.value,
            reason,
            "owner confirmation or manual task required",
        ),
        risk=risk,
        requires_confirmation=True,
    )


def _owner_confirmation_fallback(target: str, reason: str, path: str) -> BridgeFallback:
    return BridgeFallback(
        state=OWNER_CONFIRMATION_FALLBACK,
        reason=reason,
        path=path,
        owner_confirmation={
            "target": redact_text(target),
            "state": "pending",
            "item_type": "manual_task",
            "reason": redact_text(reason),
        },
    )


def _risk_for_operation(operation: str) -> RiskLevel:
    lowered = operation.lower()
    if any(term in lowered for term in HIGH_RISK_TERMS):
        return RiskLevel.HIGH
    return RiskLevel.LOW

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final

from .bridge_profiles import (
    InventoryCapability,
    find_inventory_capability,
    load_bridge_profile_inventory,
    text_field,
)
from .operation_classifier import classify_operation
from .operation_policy import RiskLevel
from .redaction import redact_json, redact_text

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

OWNER_CONFIRMATION_FALLBACK: Final = "owner_confirmation_or_manual_task"
LOCAL_FILE_ADAPTER: Final = "local_files_adapter"


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
class BridgeRequest:
    target: BridgeTarget | str
    inventory: JSONValue | None = None
    inputs: JSONObject | None = None
    operation: str = ""


@dataclass(frozen=True, slots=True)
class BridgeInvocation:
    capability: str
    status: str
    invocation_path: str
    fallback: str
    confidence: float
    mutation_allowed: bool
    required_owner_confirmation: JSONObject
    adapter: str | None = None

    def to_dict(self) -> JSONObject:
        payload: JSONObject = {
            "capability": redact_text(self.capability),
            "status": redact_text(self.status),
            "invocation_path": redact_text(self.invocation_path),
            "fallback": redact_text(self.fallback),
            "confidence": self.confidence,
            "mutation_allowed": self.mutation_allowed,
            "required_owner_confirmation": redact_json(self.required_owner_confirmation),
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
    inventory = load_bridge_profile_inventory() if request.inventory is None else request.inventory
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
    row = find_inventory_capability(inventory, capability)
    if row is None:
        return _missing_plan(parsed_target, safe_inputs, risk, "inventory capability absent")
    if not row.available:
        return _missing_plan(parsed_target, safe_inputs, risk, row.fallback)

    return BridgePlan(
        target=parsed_target.value,
        available=True,
        invocation=_invocation_for(parsed_target, row, risk),
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


def _invocation_for(
    target: BridgeTarget,
    row: InventoryCapability,
    risk: RiskLevel,
) -> BridgeInvocation:
    adapter = LOCAL_FILE_ADAPTER if target is BridgeTarget.FILESYSTEM else None
    return BridgeInvocation(
        capability=row.capability,
        status=row.status,
        invocation_path=row.invocation_path,
        fallback=row.fallback,
        confidence=row.confidence,
        mutation_allowed=False,
        required_owner_confirmation=_required_confirmation_for(row, risk),
        adapter=adapter,
    )


def _required_confirmation_for(row: InventoryCapability, risk: RiskLevel) -> JSONObject:
    if risk is RiskLevel.HIGH:
        return {
            "state": "required",
            "reason": "high-impact external handoff requires owner confirmation",
        }
    state = text_field(row.required_owner_confirmation, "state")
    reason = text_field(row.required_owner_confirmation, "reason")
    if state == "required":
        return {"state": state, "reason": reason or "owner confirmation required"}
    return {"state": "not_required", "reason": "read-only low-risk handoff"}


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
    return classify_operation(operation).risk_level

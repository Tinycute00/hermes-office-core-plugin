from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

AVAILABLE_STATUSES: Final = frozenset(("available", "installed"))
PROFILE_INVENTORY_PATH: Final = (
    Path(__file__).resolve().parents[1] / "docs" / "inventory" / "skill-mcp-inventory.json"
)


@dataclass(frozen=True, slots=True)
class InventoryCapability:
    capability: str
    status: str
    invocation_path: str
    fallback: str
    confidence: float
    mutation_allowed: bool
    required_owner_confirmation: JSONObject

    @property
    def available(self) -> bool:
        return self.status.lower() in AVAILABLE_STATUSES


def load_bridge_profile_inventory() -> JSONValue:
    return json.loads(PROFILE_INVENTORY_PATH.read_text(encoding="utf-8"))


def find_inventory_capability(
    inventory: JSONValue,
    capability: str,
) -> InventoryCapability | None:
    if not isinstance(inventory, list):
        return None
    for item in inventory:
        row = parse_inventory_row(item)
        if row is not None and row.capability == capability:
            return row
    return None


def parse_inventory_row(item: JSONValue) -> InventoryCapability | None:
    if not isinstance(item, dict):
        return None
    capability = text_field(item, "capability")
    status = text_field(item, "status")
    invocation_path = text_field(item, "invocation_path")
    fallback = text_field(item, "fallback")
    confidence = confidence_field(item)
    mutation_allowed = mutation_allowed_field(item)
    required_owner_confirmation = owner_confirmation_field(item)
    if None in (capability, status, invocation_path, fallback, confidence):
        return None
    return InventoryCapability(
        capability=capability,
        status=status,
        invocation_path=invocation_path,
        fallback=fallback,
        confidence=confidence,
        mutation_allowed=mutation_allowed,
        required_owner_confirmation=required_owner_confirmation,
    )


def text_field(item: JSONObject, key: str) -> str | None:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def confidence_field(item: JSONObject) -> float | None:
    value = item.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    score = float(value)
    if 0.0 <= score <= 1.0:
        return score
    return None


def mutation_allowed_field(item: JSONObject) -> bool:
    value = item.get("mutation_allowed")
    if isinstance(value, bool):
        return value
    return False


def owner_confirmation_field(item: JSONObject) -> JSONObject:
    value = item.get("required_owner_confirmation")
    if isinstance(value, dict):
        state = text_field(value, "state")
        reason = text_field(value, "reason")
        if state is not None and reason is not None:
            return {"state": state, "reason": reason}
    return {
        "state": "required_for_mutation",
        "reason": "owner confirmation required before external mutation",
    }

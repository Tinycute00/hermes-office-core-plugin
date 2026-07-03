# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# --- How to run ---
# .\.venv\Scripts\python.exe scripts\qa\validate_inventory.py
#   --inventory docs\inventory\skill-mcp-inventory.json
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, NoReturn, TypeAlias, TypedDict, assert_never

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
InventoryStatus: TypeAlias = Literal["installed", "available", "missing", "unknown"]

REQUIRED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "capability",
        "status",
        "source",
        "invocation_path",
        "fallback",
        "confidence",
        "mutation_allowed",
        "notes",
        "required_owner_confirmation",
    },
)
REQUIRED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "Kanban",
        "Excel/spreadsheet",
        "Word/docx",
        "PDF/OCR",
        "PowerPoint/PPT",
        "Google Workspace / Drive / Docs / Sheets / Slides",
        "filesystem/local files",
        "GitHub",
        "Linear",
        "Outlook/Gmail/Slack communications",
        "MCP general bridge",
        "Hermes plugin skills / plugin namespace",
    },
)


class InventoryRow(TypedDict):
    capability: str
    status: InventoryStatus
    source: str
    invocation_path: str
    fallback: str
    confidence: float
    mutation_allowed: bool
    notes: str
    required_owner_confirmation: dict[str, JsonValue]


class InventoryContractError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise InventoryContractError(message)


def require_text(row: Mapping[str, JsonValue], field: str) -> str:
    match row.get(field):
        case str() as value if value.strip():
            return value
        case _:
            fail(f"{row.get('capability', '<unknown>')}: {field} must be a non-empty string")


def parse_status(row: Mapping[str, JsonValue]) -> InventoryStatus:
    value = require_text(row, "status")
    match value:
        case "installed" | "available" | "missing" | "unknown":
            return value
        case _:
            fail(f"{require_text(row, 'capability')}: unsupported status {value!r}")


def require_confidence(row: Mapping[str, JsonValue]) -> float:
    value = row.get("confidence")
    match value:
        case bool():
            fail(f"{require_text(row, 'capability')}: confidence must be numeric")
        case int() | float():
            confidence = float(value)
            if 0.0 <= confidence <= 1.0:
                return confidence
            fail(f"{require_text(row, 'capability')}: confidence must be between 0 and 1")
        case _:
            fail(f"{require_text(row, 'capability')}: confidence must be numeric")


def require_mutation_lock(row: Mapping[str, JsonValue]) -> bool:
    value = row.get("mutation_allowed")
    match value:
        case False:
            return False
        case _:
            fail(f"{require_text(row, 'capability')}: mutation_allowed must be false")


def require_owner_confirmation(row: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    value = row.get("required_owner_confirmation")
    match value:
        case {"state": str() as state, "reason": str() as reason} if (
            state.strip() and reason.strip()
        ):
            if state not in {"required", "required_for_mutation"}:
                fail(f"{require_text(row, 'capability')}: owner confirmation must fail closed")
            return {"state": state, "reason": reason}
        case _:
            fail(f"{require_text(row, 'capability')}: owner confirmation rules must be present")


def parse_row(raw_row: Mapping[str, JsonValue]) -> InventoryRow:
    extra = frozenset(raw_row) - REQUIRED_FIELDS
    missing = REQUIRED_FIELDS - frozenset(raw_row)
    if missing or extra:
        fail(f"row fields mismatch: missing={sorted(missing)} extra={sorted(extra)}")

    row = InventoryRow(
        capability=require_text(raw_row, "capability"),
        status=parse_status(raw_row),
        source=require_text(raw_row, "source"),
        invocation_path=require_text(raw_row, "invocation_path"),
        fallback=require_text(raw_row, "fallback"),
        confidence=require_confidence(raw_row),
        mutation_allowed=require_mutation_lock(raw_row),
        notes=require_text(raw_row, "notes"),
        required_owner_confirmation=require_owner_confirmation(raw_row),
    )
    match row["status"]:
        case "missing" | "unknown":
            require_text(row, "fallback")
        case "installed" | "available":
            require_text(row, "source")
        case unreachable:
            assert_never(unreachable)
    return row


def load_rows(inventory_path: Path) -> Sequence[InventoryRow]:
    try:
        raw = json.loads(inventory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")

    match raw:
        case list() as raw_rows:
            rows: list[InventoryRow] = []
            for raw_row in raw_rows:
                match raw_row:
                    case dict() as raw_mapping:
                        rows.append(parse_row(raw_mapping))
                    case _:
                        fail("inventory must contain only objects")
            return rows
        case _:
            fail("inventory root must be a JSON array")


def validate_inventory(inventory_path: Path) -> Sequence[InventoryRow]:
    rows = load_rows(inventory_path)
    capabilities = {row["capability"] for row in rows}
    missing = REQUIRED_CAPABILITIES - capabilities
    extras = capabilities - REQUIRED_CAPABILITIES
    if missing or extras:
        fail(f"capability coverage mismatch: missing={sorted(missing)} extra={sorted(extras)}")
    if len(rows) != len(capabilities):
        fail("inventory contains duplicate capabilities")
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the Todo 5 skill/MCP inventory.")
    parser.add_argument("--inventory", required=True, type=Path)
    return parser


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    inventory_path = args.inventory.resolve()
    print(f"command: validate_inventory.py --inventory {args.inventory}")
    print(f"cwd: {Path.cwd()}")
    print(f"inventory: {inventory_path}")
    try:
        rows = validate_inventory(inventory_path)
    except InventoryContractError as exc:
        print(f"assertion: failed: {exc}")
        print("exit: 1")
        return 1
    print(f"assertion: passed: {len(rows)} required capabilities covered")
    print("exit: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

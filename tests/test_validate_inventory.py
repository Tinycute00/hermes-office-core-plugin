from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from scripts.qa.validate_inventory import InventoryContractError, validate_inventory

if TYPE_CHECKING:
    from office_core_plugin.handler_contract import JSONValue


PLUGIN_ROOT: Final = Path(__file__).resolve().parents[1]
INVENTORY_JSON: Final = PLUGIN_ROOT / "docs" / "inventory" / "skill-mcp-inventory.json"
INVENTORY_MD: Final = PLUGIN_ROOT / "docs" / "inventory" / "skill-mcp-inventory.md"


def test_characterizes_current_json_inventory_as_valid() -> None:
    # Given: the current checked-in inventory JSON fixture.
    inventory_path = INVENTORY_JSON

    # When: the inventory validator runs against it.
    rows = validate_inventory(inventory_path)

    # Then: the existing required capability set is covered exactly once.
    assert len(rows) == 12
    assert {row["capability"] for row in rows} == {
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
    }


def test_inventory_profiles_require_fail_closed_profile_fields(tmp_path: Path) -> None:
    # Given: an otherwise valid row missing the deterministic handoff safety fields.
    inventory = _valid_inventory_rows()
    row = inventory[0]
    del row["mutation_allowed"]
    del row["required_owner_confirmation"]
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    # When/Then: the validator rejects the stale profile shape.
    with pytest.raises(InventoryContractError, match="row fields mismatch"):
        validate_inventory(inventory_path)


def test_inventory_profiles_disallow_mutation_allowed_true(tmp_path: Path) -> None:
    # Given: a high-impact external handoff profile tries to allow mutation.
    inventory = _valid_inventory_rows()
    github = next(row for row in inventory if row["capability"] == "GitHub")
    github["mutation_allowed"] = True
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    # When/Then: the validator rejects any mutable bridge profile.
    with pytest.raises(InventoryContractError, match="mutation_allowed must be false"):
        validate_inventory(inventory_path)


def test_inventory_profiles_require_owner_confirmation_rules(tmp_path: Path) -> None:
    # Given: an installed external profile omits required owner-confirmation rules.
    inventory = _valid_inventory_rows()
    github = next(row for row in inventory if row["capability"] == "GitHub")
    github["required_owner_confirmation"] = {"state": "not_required"}
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    # When/Then: the validator rejects non-fail-closed external handoff rules.
    with pytest.raises(InventoryContractError, match="owner confirmation"):
        validate_inventory(inventory_path)


def test_markdown_and_json_inventory_statuses_stay_aligned() -> None:
    # Given: human markdown and JSON inventory fixtures.
    markdown_rows = _markdown_inventory_statuses(INVENTORY_MD.read_text(encoding="utf-8"))
    json_rows = {
        row["capability"]: row["status"]
        for row in json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
    }

    # When/Then: capability/status facts match in both sources.
    assert markdown_rows == json_rows


def _valid_inventory_rows() -> list[dict[str, JSONValue]]:
    return json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))


def _markdown_inventory_statuses(markdown: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in markdown.splitlines():
        if not line.startswith("| ") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] == "Capability":
            continue
        statuses[cells[0]] = cells[1]
    return statuses

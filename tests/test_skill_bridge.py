from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

from office_core_plugin.bridge_planner import (
    OWNER_CONFIRMATION_FALLBACK,
    BridgeRequest,
    BridgeTarget,
    plan_bridge_handoff,
)

if TYPE_CHECKING:
    from office_core_plugin.handler_contract import JSONValue

SYNTHETIC_SECRET: Final = "sk-test-" + "bridge-secret-12345"


def _inventory_row(
    capability: str,
    status: str,
    invocation_path: str,
    fallback: str,
) -> dict[str, JSONValue]:
    return {
        "capability": capability,
        "status": status,
        "source": "unit proof",
        "invocation_path": invocation_path,
        "fallback": fallback,
        "confidence": 0.86,
        "notes": "unit row",
    }


def test_available_document_target_returns_inventory_declared_invocation() -> None:
    # Given: an inventory row proving the document bridge capability.
    inventory = [
        _inventory_row(
            "Word/docx",
            "available",
            "ocr-and-documents guidance plus local file tooling",
            "manual document handoff",
        ),
    ]

    # When: a read-only document handoff is planned.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.DOCUMENT,
            inventory=inventory,
            inputs={"document_id": "invoice-template"},
            operation="read document",
        ),
    ).to_dict()

    # Then: invocation data is present and only names the inventory-declared capability.
    assert plan["target"] == "document"
    assert plan["available"] is True
    assert plan["invocation"] == {
        "capability": "Word/docx",
        "status": "available",
        "invocation_path": "ocr-and-documents guidance plus local file tooling",
        "fallback": "manual document handoff",
        "confidence": 0.86,
        "mutation_allowed": False,
        "required_owner_confirmation": {
            "state": "not_required",
            "reason": "read-only low-risk handoff",
        },
    }
    assert plan["inputs"] == {"document_id": "invoice-template"}
    assert plan["fallback"]["state"] == "not_needed"
    assert plan["risk"] == "low"
    assert plan["requires_confirmation"] is False


@pytest.mark.parametrize("target", list(BridgeTarget))
def test_known_todo_11_targets_return_structured_plans(target: BridgeTarget) -> None:
    # Given: inventory rows for every Todo 11 bridge target.
    inventory = [
        _inventory_row("Kanban", "installed", "kanban surface", "manual kanban task"),
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
        _inventory_row("Excel/spreadsheet", "available", "sheets bridge", "manual sheet handoff"),
        _inventory_row("PowerPoint/PPT", "installed", "deck bridge", "manual deck handoff"),
        _inventory_row("PDF/OCR", "installed", "ocr bridge", "manual OCR handoff"),
        _inventory_row(
            "Google Workspace / Drive / Docs / Sheets / Slides",
            "installed",
            "workspace bridge",
            "manual workspace handoff",
        ),
        _inventory_row("filesystem/local files", "available", "file bridge", "manual file handoff"),
        _inventory_row("Linear", "available", "linear mcp bridge", "manual Linear handoff"),
        _inventory_row("GitHub", "installed", "github bridge", "manual GitHub handoff"),
    ]

    # When: the target handoff is planned.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=target,
            inventory=inventory,
            inputs={"request_id": target.value},
            operation="read target",
        ),
    ).to_dict()

    # Then: every target returns the stable handoff shape with inventory invocation data.
    assert plan["target"] == target.value
    assert plan["available"] is True
    assert plan["invocation"] is not None
    assert set(plan) == {
        "target",
        "available",
        "invocation",
        "inputs",
        "fallback",
        "risk",
        "requires_confirmation",
    }


def test_inventory_lacking_kanban_creates_owner_confirmation_item_without_tool_call() -> None:
    # Given: an inventory fixture that has no Kanban capability.
    inventory = [
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
    ]

    # When: a Kanban handoff is planned.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target="kanban",
            inventory=inventory,
            inputs={"summary": "Create follow-up task"},
            operation="create task",
        ),
    ).to_dict()

    # Then: the planner fails closed with an owner-confirmation/manual-task fallback.
    assert plan["target"] == "kanban"
    assert plan["available"] is False
    assert plan["invocation"] is None
    assert plan["fallback"]["state"] == OWNER_CONFIRMATION_FALLBACK
    assert plan["fallback"]["owner_confirmation"]["target"] == "kanban"
    assert plan["fallback"]["owner_confirmation"]["state"] == "pending"
    assert plan["requires_confirmation"] is True


def test_malformed_inventory_and_unknown_target_fail_safely_without_secret_leak() -> None:
    # Given: malformed and untrusted inventory labels containing synthetic secret text.
    inventory: list[JSONValue] = [
        "not a row",
        {
            "capability": f"Unknown Authorization: Bearer {SYNTHETIC_SECRET}",
            "status": "available",
            "source": f"secret={SYNTHETIC_SECRET}",
            "invocation_path": "fake-skill",
            "fallback": f"manual token={SYNTHETIC_SECRET}",
            "confidence": "bad",
            "notes": "untrusted",
        },
    ]

    # When: an unsupported target is planned from malformed inventory.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target="unsupported_target",
            inventory=inventory,
            inputs={"label": f"token={SYNTHETIC_SECRET}"},
            operation=f"send secret={SYNTHETIC_SECRET}",
        ),
    ).to_dict()
    raw_json = json.dumps(plan, sort_keys=True)

    # Then: the response is safe, requires owner confirmation, and leaks no raw secret.
    assert plan["target"] == "unknown"
    assert plan["available"] is False
    assert plan["invocation"] is None
    assert plan["fallback"]["state"] == OWNER_CONFIRMATION_FALLBACK
    assert plan["requires_confirmation"] is True
    assert SYNTHETIC_SECRET not in raw_json
    assert "[REDACTED]" in raw_json


def test_bridge_plan_redacts_free_text_bearer_and_api_tokens() -> None:
    # Given: available document inventory and untrusted inputs containing bare secrets.
    bearer_value = "sk-test-" + "sisyphus-secret-98765"
    api_value = "sk-test-" + "office-api-secret-24680"
    inventory = [
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
    ]

    # When: the bridge plan is serialized for display or JSON transport.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.DOCUMENT,
            inventory=inventory,
            inputs={
                "label": f"Bearer {bearer_value}",
                "comment": f"paste api token {api_value} into the note",
                "normal_office_text": "please review the quarterly planning memo",
            },
            operation="read document",
        ),
    ).to_dict()
    raw_json = json.dumps(plan, sort_keys=True)

    # Then: credential-shaped free text is redacted without erasing normal prose.
    assert bearer_value not in raw_json
    assert api_value not in raw_json
    assert "[REDACTED]" in raw_json
    assert plan["inputs"]["normal_office_text"] == "please review the quarterly planning memo"


def test_high_risk_write_or_send_handoffs_require_confirmation() -> None:
    # Given: available document and GitHub inventory rows.
    inventory = [
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
        _inventory_row("GitHub", "installed", "gh", "manual GitHub draft"),
    ]

    # When: write-like and send-like handoffs are planned.
    document_plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.DOCUMENT,
            inventory=inventory,
            inputs={"path": "proposal.docx"},
            operation="update document",
        ),
    ).to_dict()
    github_plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.GITHUB_MCP,
            inventory=inventory,
            inputs={"issue": "release note"},
            operation="send issue update",
        ),
    ).to_dict()

    # Then: both are draft handoffs that require explicit confirmation.
    assert document_plan["available"] is True
    assert document_plan["risk"] == "high"
    assert document_plan["requires_confirmation"] is True
    assert github_plan["available"] is True
    assert github_plan["risk"] == "high"
    assert github_plan["requires_confirmation"] is True


def test_upload_handoff_uses_shared_high_impact_policy() -> None:
    # Given: available document inventory for an upload-style operation.
    inventory = [
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
    ]

    # When: the planner evaluates an upload handoff.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.DOCUMENT,
            inventory=inventory,
            inputs={"path": "report.docx"},
            operation="upload report",
        ),
    ).to_dict()

    # Then: upload is high impact and requires confirmation even when available.
    assert plan["available"] is True
    assert plan["risk"] == "high"
    assert plan["requires_confirmation"] is True


def test_filesystem_target_uses_local_file_adapter_only_when_inventory_says_available() -> None:
    # Given: available filesystem inventory and a second fixture without that row.
    available_inventory = [
        _inventory_row(
            "filesystem/local files",
            "available",
            "Hermes file/terminal toolsets",
            "manual file path request",
        ),
    ]
    missing_inventory = [
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
    ]

    # When: filesystem handoffs are planned for both inventories.
    available_plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.FILESYSTEM,
            inventory=available_inventory,
            inputs={"allowed_root": "C:/workspace"},
            operation="discover files",
        ),
    ).to_dict()
    missing_plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.FILESYSTEM,
            inventory=missing_inventory,
            inputs={"allowed_root": "C:/workspace"},
            operation="discover files",
        ),
    ).to_dict()

    # Then: only the available inventory delegates to the local-file adapter.
    assert available_plan["available"] is True
    assert available_plan["invocation"]["capability"] == "filesystem/local files"
    assert available_plan["invocation"]["adapter"] == "local_files_adapter"
    assert missing_plan["available"] is False
    assert missing_plan["invocation"] is None
    assert missing_plan["fallback"]["state"] == OWNER_CONFIRMATION_FALLBACK


def test_missing_capability_does_not_invent_unavailable_skill_names() -> None:
    # Given: inventory with a missing Kanban row that mentions no callable skill.
    inventory = [
        _inventory_row("Kanban", "missing", "No proven path", "manual task"),
    ]

    # When: a Kanban handoff is planned.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.KANBAN,
            inventory=inventory,
            inputs={"title": "Board task"},
            operation="create task",
        ),
    ).to_dict()
    raw_json = json.dumps(plan, sort_keys=True)

    # Then: missing capability produces no invocation and no fabricated skill/tool name.
    assert plan["available"] is False
    assert plan["invocation"] is None
    assert "devops/kanban-orchestrator" not in raw_json
    assert "kanban_create" not in raw_json


def test_characterizes_current_inventory_linear_as_missing_fallback() -> None:
    # Given: the current inventory marks Linear as missing.
    inventory = [
        _inventory_row(
            "Linear",
            "missing",
            "No target Hermes invocation path is currently proven.",
            "Create owner-confirmation items or draft Linear updates for manual posting.",
        ),
    ]

    # When: the planner evaluates a Linear handoff.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.LINEAR,
            inventory=inventory,
            inputs={"issue": "LIN-123"},
            operation="create Linear issue",
        ),
    ).to_dict()

    # Then: current behavior fails closed with owner confirmation and no invocation.
    assert plan["target"] == "linear"
    assert plan["available"] is False
    assert plan["invocation"] is None
    assert plan["fallback"]["state"] == OWNER_CONFIRMATION_FALLBACK
    assert plan["fallback"]["owner_confirmation"]["state"] == "pending"


def test_unknown_linear_string_fails_closed_with_manual_fallback() -> None:
    # Given: no inventory proof for a misleading unknown Linear-like target string.
    inventory = [
        _inventory_row("Linear", "missing", "No proven path", "manual Linear handoff"),
    ]

    # When: the requested target is not a supported planner enum value.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target="linear-live-api",
            inventory=inventory,
            inputs={"issue": "LIN-999"},
            operation="create Linear issue",
        ),
    ).to_dict()

    # Then: the planner does not treat the unknown capability as success.
    assert plan["target"] == "unknown"
    assert plan["available"] is False
    assert plan["invocation"] is None
    assert plan["fallback"]["state"] == OWNER_CONFIRMATION_FALLBACK
    assert plan["fallback"]["owner_confirmation"]["state"] == "pending"


def test_high_impact_external_profile_is_mutation_locked_and_owner_confirmed() -> None:
    # Given: a high-impact external GitHub profile marked installed.
    inventory = [
        _inventory_row("GitHub", "installed", "github bridge", "manual GitHub draft"),
    ]

    # When: the planner evaluates a send/write handoff.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.GITHUB_MCP,
            inventory=inventory,
            inputs={"issue": "release note"},
            operation="send issue update",
        ),
    ).to_dict()

    # Then: the deterministic profile cannot mutate and requires owner confirmation.
    assert plan["available"] is True
    assert plan["invocation"]["mutation_allowed"] is False
    assert plan["invocation"]["required_owner_confirmation"]["state"] == "required"
    assert plan["requires_confirmation"] is True


def test_available_document_profile_uses_required_profile_shape() -> None:
    # Given: an available document profile.
    inventory = [
        _inventory_row("Word/docx", "available", "document bridge", "manual document handoff"),
    ]

    # When: a document handoff is planned.
    plan = plan_bridge_handoff(
        BridgeRequest(
            target=BridgeTarget.DOCUMENT,
            inventory=inventory,
            inputs={"document_id": "template-update-draft"},
            operation="read document",
        ),
    ).to_dict()

    # Then: the invocation is a complete deterministic bridge profile.
    assert plan["invocation"] == {
        "capability": "Word/docx",
        "status": "available",
        "invocation_path": "document bridge",
        "fallback": "manual document handoff",
        "confidence": 0.86,
        "mutation_allowed": False,
        "required_owner_confirmation": {
            "state": "not_required",
            "reason": "read-only low-risk handoff",
        },
    }

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from office_core_plugin.completion_validation import (
    CompletionDraft,
    CompletionField,
    CompletionSource,
    DeliverableType,
    validate_document,
)
from office_core_plugin.data_maps import SourceSelectionResult
from office_core_plugin.e2e_fixtures import source_record
from office_core_plugin.e2e_workflows import (
    build_ambiguous_latest_main_probe,
    build_office_correctness_workflows,
)
from office_core_plugin.plugin import register_tool_definitions
from office_core_plugin.registry_models import OwnerConfirmationItem, OwnerConfirmationState
from office_core_plugin.tool_handlers import TOOL_DEFINITIONS
from office_core_plugin.workflow_plan_contract import draft_workflow_plan

if TYPE_CHECKING:
    from pathlib import Path

    from office_core_plugin.handler_contract import JSONValue, SafeToolHandler


class FakeHermesContext:
    def __init__(self) -> None:
        self.handlers: dict[str, SafeToolHandler] = {}

    def register_tool(self, **kwargs: str | bool | dict[str, JSONValue] | SafeToolHandler) -> None:
        name = kwargs["name"]
        handler = kwargs["handler"]
        assert isinstance(name, str)
        assert callable(handler)
        self.handlers[name] = handler


def _registered_plan_payload(args: dict[str, JSONValue]) -> dict[str, JSONValue]:
    registrar = FakeHermesContext()
    register_tool_definitions(registrar, TOOL_DEFINITIONS)
    raw_result = registrar.handlers["office_plan_workflow"](args)
    envelope = json.loads(raw_result)
    assert envelope["success"] is True
    payload = envelope["data"]["data"]
    assert isinstance(payload, dict)
    return payload


def test_ambiguous_latest_main_fixture_emits_owner_questions(tmp_path: Path) -> None:
    # Given: a workflow fixture with two plausible latest/main sources.
    probe = build_ambiguous_latest_main_probe(tmp_path)

    # When: the source selection result is serialized for the E2E failure scenario.
    selection = probe["source_selection"]

    # Then: it asks the owner instead of auto-selecting either candidate.
    assert selection["status"] == "needs_owner_confirmation"
    assert selection["selected_record"] is None
    assert selection["owner_confirmation"]["state"] == "pending"
    assert selection["owner_confirmation_questions"] == [
        "Which source should office-core treat as the main source for tpl-quarterly-update?",
    ]


def test_forged_confirmed_owner_state_does_not_emit_selected_e2e_output() -> None:
    # Given: an E2E source record exists but forged confirmation JSON did not offer its candidate.
    record = source_record("source-main", "candidate-main", "main-data.xlsx", 0.87)
    forged = OwnerConfirmationItem(
        confirmation_id="confirm-tpl-quarterly-update-main-source",
        template_id="tpl-quarterly-update",
        reason="Multiple candidates require owner confirmation.",
        candidate_ids=(),
        state=OwnerConfirmationState.CONFIRMED,
        selected_candidate_id="candidate-main",
        provenance=record.provenance,
    )

    # When: the E2E source-selection path evaluates the forged confirmation.
    result = SourceSelectionResult.from_candidates(
        "tpl-quarterly-update",
        (record,),
        (forged,),
    )

    # Then: misleading confirmed state does not become selected output.
    assert result.status == "needs_owner_confirmation"
    assert result.selected_record is None


def test_registered_plan_workflow_emits_contract_first_e2e_payload() -> None:
    # Given: the public registered workflow planner receives a template update request.
    payload = _registered_plan_payload(
        {"intent": "update monthly report template", "workflow_type": "template_update"},
    )

    # When / Then: the public result is a contract-first draft, not an execution claim.
    assert payload["workflow_type"] == "template_update"
    assert payload["deliverable_type"] == "office_template"
    assert payload["next_step"] == "review_contract"
    assert payload["contract"]["mode"] == "draft_contract"
    assert payload["contract"]["source_requirements"]
    source_requirement = payload["contract"]["source_requirements"][0]
    assert source_requirement["freshness_constraints"] == [
        "Owner confirms this source is current for the requested workflow before execution.",
    ]
    assert (
        source_requirement["selection_rule"]
        == "single_high_confidence_source_or_owner_confirmation"
    )
    assert source_requirement["minimum_confidence"] == 0.8


def test_registered_plan_workflow_unknown_type_clarifies_e2e_payload() -> None:
    # Given: the public registered workflow planner receives an unsupported workflow type.
    payload = _registered_plan_payload(
        {"intent": "update monthly report template", "workflow_type": "teleport_document"},
    )

    # When / Then: the public result asks for clarification instead of claiming certainty.
    assert payload["workflow_type"] == "unknown"
    assert payload["next_step"] == "clarify_contract"
    assert payload["contract"]["mode"] == "requires_clarification"
    assert payload["unresolved_questions"]


def test_completion_validation_accepts_contract_first_workflow_e2e_payload() -> None:
    # Given: the typed workflow planner emits a contract with sanitized source metadata.
    plan = draft_workflow_plan(
        {
            "intent": "create monthly report draft from source package",
            "workflow_type": "report_generation",
        },
        "2026-07-03T00:00:00Z",
    )
    source = plan.contract.source_requirements[0]
    draft = CompletionDraft(
        deliverable_type=DeliverableType.DOCUMENT,
        contract=plan.contract,
        instructions=("Create a draft report handoff; no external send.",),
        required_placeholders=("report_month",),
        fields=(
            CompletionField(
                "report_month",
                "month label",
                source.label,
                source.evidence_hash,
                0.91,
            ),
        ),
        sources=(CompletionSource(source.label, source.evidence_hash, source.summary),),
        bridge_target="document",
        operation_intent="draft monthly report",
        owner_confirmed=False,
        external_side_effect=False,
    )

    # When: completion validation runs on that sanitized draft metadata.
    result = validate_document(draft).to_dict()

    # Then: validation returns the public completion-result contract without live mutation.
    assert result["success"] is True
    assert result["blocking_issues"] == []
    assert result["evidence"] == [source.evidence_hash]


def test_office_correctness_fixtures_are_draft_only_end_to_end() -> None:
    # Given: the sanitized E2E workflow fixture pack is built without live Office data.
    workflows = build_office_correctness_workflows()

    # When: the workflows are indexed by their stable fixture IDs.
    by_id = {str(item["fixture_id"]): item for item in workflows}

    # Then: every required workflow carries the full draft-only correctness chain.
    assert set(by_id) == {
        "monthly_report_template_update",
        "messy_spreadsheet_data_package",
        "approved_reusable_data_to_deck",
        "external_send_preview",
    }
    for workflow in by_id.values():
        contract = workflow["contract"]
        source_data_map = workflow["source_data_map"]
        bridge_plan = workflow["bridge_plan"]
        validation = workflow["validation_result"]
        handoff = workflow["draft_handoff"]

        assert str(contract["contract_id"]).startswith("office-e2e-")
        assert contract["provenance"]
        assert contract["confidence"]["band"] in {"medium", "high"}
        assert source_data_map["sources"][0]["evidence_hash"] in validation["evidence"]
        assert source_data_map["fields"][0]["confidence"]["band"] in {"medium", "high"}
        assert validation["checks"] == [
            "deliverable_type",
            "bridge_target",
            "placeholders",
            "source_provenance",
            "field_confidence",
            "operation_policy",
            "secret_redaction",
            "draft_only",
        ]
        assert bridge_plan["target"] == contract["bridge_target"]
        assert bridge_plan["risk"] in {"low", "medium", "high"}
        assert bridge_plan["requires_confirmation"] is (
            bridge_plan["risk"] == "high" or not bridge_plan["available"]
        )
        invocation = bridge_plan["invocation"]
        if invocation is None:
            assert bridge_plan["fallback"]["owner_confirmation"]["state"] == "pending"
        else:
            assert invocation["mutation_allowed"] is False
            assert invocation["required_owner_confirmation"]["state"] in {
                "not_required",
                "required",
            }
        assert handoff["state"] == "draft_only"
        assert handoff["external_side_effect"] is False
        assert workflow["external_side_effect"] is False

    assert by_id["messy_spreadsheet_data_package"]["owner_confirmation"]["state"] == "pending"
    assert by_id["messy_spreadsheet_data_package"]["validation_result"][
        "requires_owner_confirmation"
    ] is True
    assert by_id["approved_reusable_data_to_deck"]["owner_confirmation"]["state"] == "confirmed"
    assert by_id["approved_reusable_data_to_deck"]["validation_result"]["success"] is True
    external_preview = by_id["external_send_preview"]
    assert external_preview["owner_confirmation"]["state"] == "pending"
    assert external_preview["bridge_plan"]["requires_confirmation"] is True
    assert external_preview["bridge_plan"]["risk"] == "high"
    assert external_preview["validation_result"]["requires_owner_confirmation"] is True
    assert external_preview["validation_result"]["success"] is False

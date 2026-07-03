from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from .redaction import redact_text
from .task_contract import CorrectnessCriteria, OfficeTaskContract, SourceRequirement

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue

MIN_ACTIONABLE_INTENT_WORDS: Final = 3
CLARIFICATION_MODE: Final = "requires_clarification"
CONTRACT_MODE: Final = "draft_contract"
WORKFLOW_PROFILES: Final[dict[str, tuple[str, str, str]]] = {
    "template_update": ("office_template", "office_template_bridge", "medium"),
    "report_generation": ("office_report_draft", "office_report_bridge", "medium"),
    "data_review": ("office_data_review", "office_data_bridge", "low"),
}


@dataclass(frozen=True, slots=True)
class WorkflowPlanDraft:
    contract: OfficeTaskContract
    unresolved_questions: tuple[str, ...]

    def to_summary(self) -> JSONObject:
        contract_data = self.contract.to_dict()
        return {
            "bridge_target_recommendation": contract_data["bridge_target"],
            "contract": contract_data,
            "correctness_criteria": contract_data["correctness_criteria"],
            "deliverable_type": contract_data["deliverable_type"],
            "mode": contract_data["mode"],
            "next_step": contract_data["next_step"],
            "operation_mode": contract_data["mode"],
            "owner_confirmation_items": contract_data["owner_confirmations"],
            "source_requirements": contract_data["source_requirements"],
            "unresolved_questions": contract_data["unresolved_questions"],
            "validation_plan": contract_data["validation_plan"],
            "workflow_type": contract_data["workflow_type"],
        }


def draft_workflow_plan(args: JSONObject, observed_at: str) -> WorkflowPlanDraft:
    intent = _text_arg(args, "intent")
    workflow_type = _workflow_type(args)
    profile = _profile(workflow_type)
    unresolved_questions = _unresolved_questions(intent, workflow_type)
    requires_clarification = _is_weak_intent(intent) or profile is None
    confidence = 0.42 if requires_clarification else 0.78
    mode = CLARIFICATION_MODE if requires_clarification else CONTRACT_MODE
    next_step = "clarify_contract" if requires_clarification else "review_contract"
    contract = OfficeTaskContract(
        contract_id=_contract_id(intent, workflow_type),
        workflow_type="unknown" if profile is None else workflow_type,
        deliverable_type="office_workflow_draft" if profile is None else profile[0],
        intended_audience="request owner and designated office reviewers",
        unresolved_questions=unresolved_questions
        or (
            "Confirm owner approval path before execution.",
        ),
        source_requirements=_source_requirements(intent, workflow_type),
        correctness_criteria=_correctness_criteria(),
        risk="medium" if profile is None else profile[2],
        owner_confirmations=_owner_confirmations(mode),
        validation_plan=_validation_plan(mode),
        bridge_target="office_clarification_bridge" if profile is None else profile[1],
        mode=mode,
        provenance=("tool_args:redacted_intent", f"observed_at:{observed_at}"),
        confidence=confidence,
        next_step=next_step,
    )
    return WorkflowPlanDraft(contract=contract, unresolved_questions=unresolved_questions)


def _profile(workflow_type: str | None) -> tuple[str, str, str] | None:
    if workflow_type is None:
        return None
    return WORKFLOW_PROFILES.get(workflow_type)


def _workflow_type(args: JSONObject) -> str | None:
    value = _text_arg(args, "workflow_type")
    if value is None:
        return None
    return value.strip().lower()


def _unresolved_questions(intent: str | None, workflow_type: str | None) -> tuple[str, ...]:
    questions: list[str] = []
    if _is_weak_intent(intent):
        questions.append("What specific intent and office outcome should this workflow produce?")
    if workflow_type not in WORKFLOW_PROFILES:
        questions.append("Which supported workflow type should the planner use?")
    questions.append("Which owner must approve the draft contract before execution?")
    return tuple(questions)


def _source_requirements(
    intent: str | None,
    workflow_type: str | None,
) -> tuple[SourceRequirement, ...]:
    digest = _digest("source", intent, workflow_type)
    return (
        SourceRequirement(
            label="redacted owner intent summary",
            uri=f"state://office-plan/source/{digest[:16]}",
            evidence_hash=digest,
            summary="Owner intent metadata only; raw document text and credentials are not stored.",
            freshness_constraints=(
                "Owner confirms this source is current for the requested workflow "
                "before execution.",
            ),
            selection_rule="single_high_confidence_source_or_owner_confirmation",
            minimum_confidence=0.8,
        ),
    )


def _correctness_criteria() -> CorrectnessCriteria:
    return CorrectnessCriteria(
        structure_openability=(
            "Draft deliverable can be opened by the target Office application.",
        ),
        data_provenance=("Every referenced source has a sanitized URI and evidence hash.",),
        format_template=("Output preserves the requested template or format constraints.",),
        delivery_approval=("No external delivery occurs before explicit owner confirmation.",),
    )


def _owner_confirmations(mode: str) -> tuple[str, ...]:
    if mode == CLARIFICATION_MODE:
        return ("Owner answers unresolved questions before planning continues.",)
    return ("Owner reviews and approves the draft contract before bridge execution.",)


def _validation_plan(mode: str) -> tuple[str, ...]:
    if mode == CLARIFICATION_MODE:
        return ("Collect owner clarification and regenerate this contract before execution.",)
    return (
        "Review contract fields for intended workflow, deliverable, and bridge target.",
        "Verify source hashes and sanitized source summaries before bridge execution.",
        "Confirm no external send or Office/SaaS operation is performed by this planner.",
    )


def _contract_id(intent: str | None, workflow_type: str | None) -> str:
    return f"office-plan-{_digest('contract', intent, workflow_type)[:16]}"


def _digest(scope: str, intent: str | None, workflow_type: str | None) -> str:
    sanitized = redact_text(intent or "missing-intent")
    encoded = f"{scope}:{sanitized}:{workflow_type or 'missing-workflow-type'}".encode()
    return sha256(encoded).hexdigest()


def _is_weak_intent(intent: str | None) -> bool:
    if intent is None:
        return True
    words = intent.split()
    return len(words) < MIN_ACTIONABLE_INTENT_WORDS


def _text_arg(args: JSONObject, key: str) -> str | None:
    value: JSONValue = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value

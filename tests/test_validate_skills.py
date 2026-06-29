from __future__ import annotations

from pathlib import Path

import pytest

from scripts.qa import validate_skills


def test_validate_skills_discovers_all_qualified_workflow_names() -> None:
    # Given: the repository root with plugin-shipped workflow skills.
    repo = Path(__file__).resolve().parents[1]

    # When: the skill validator reads the current repo.
    checks = validate_skills.run_checks(repo)

    # Then: all required workflow skills are exposed by qualified name.
    assert [check.qualified_name for check in checks] == [
        "office-core:office-template-update",
        "office-core:office-data-package",
        "office-core:office-reuse-data",
    ]


def test_validate_skills_rejects_write_send_skill_without_confirmation_gate() -> None:
    # Given: a fixture whose template skill writes output without owner confirmation.
    repo = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "bad-skills-missing-confirmation"
    )

    # When / Then: validation fails on the missing confirmation gate.
    with pytest.raises(
        validate_skills.SkillContractError,
        match="missing_confirmation_gate_for_write_send_behavior",
    ):
        validate_skills.run_checks(repo)

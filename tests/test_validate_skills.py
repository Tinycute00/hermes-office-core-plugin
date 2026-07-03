from __future__ import annotations

import shutil
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
        "office-core:office-diagnostic",
        "office-core:office-template-update",
        "office-core:office-data-package",
        "office-core:office-reuse-data",
    ]


def test_validate_skills_rejects_skill_without_contract_first_language(tmp_path: Path) -> None:
    # Given: a repo copy whose template skill no longer asks for a task contract.
    repo = _copy_skill_repo(tmp_path)
    skill_path = repo / "office_core_plugin" / "skills" / "office-template-update" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace("OfficeTaskContract", "task summary"),
        encoding="utf-8",
    )

    # When / Then: validation fails before the weakened skill can ship.
    with pytest.raises(
        validate_skills.SkillContractError,
        match="missing OfficeTaskContract requirement",
    ):
        validate_skills.run_checks(repo)


def test_validate_skills_rejects_skill_without_draft_only_boundary(tmp_path: Path) -> None:
    # Given: a repo copy whose data package skill lost draft-only language.
    repo = _copy_skill_repo(tmp_path)
    skill_path = repo / "office_core_plugin" / "skills" / "office-data-package" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace("draft-only", "preview"),
        encoding="utf-8",
    )

    # When / Then: validation fails on the missing draft-only boundary.
    with pytest.raises(
        validate_skills.SkillContractError,
        match="missing draft-only behavior",
    ):
        validate_skills.run_checks(repo)


def test_validate_skills_rejects_write_send_skill_without_confirmation_gate(tmp_path: Path) -> None:
    # Given: a fixture whose template skill writes output without owner confirmation.
    repo = _copy_skill_repo(tmp_path)
    skill_path = repo / "office_core_plugin" / "skills" / "office-reuse-data" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace("confirmation", "review"),
        encoding="utf-8",
    )

    # When / Then: validation fails on the missing confirmation gate.
    with pytest.raises(
        validate_skills.SkillContractError,
        match="missing_confirmation_gate_for_write_send_behavior",
    ):
        validate_skills.run_checks(repo)


def _copy_skill_repo(tmp_path: Path) -> Path:
    repo = Path(__file__).resolve().parents[1]
    target = tmp_path / "skill-repo"
    shutil.copytree(
        repo / "office_core_plugin" / "skills",
        target / "office_core_plugin" / "skills",
    )
    (target / "plugin.yaml").write_text("name: office-core\n", encoding="utf-8")
    (target / "office_core_plugin" / "plugin.py").write_text(
        "\n".join(validate_skills.EXPECTED_SKILLS),
        encoding="utf-8",
    )
    return target

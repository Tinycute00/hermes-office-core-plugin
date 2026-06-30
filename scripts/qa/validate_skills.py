# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# --- How to run ---
# .\.venv\Scripts\python.exe scripts\qa\validate_skills.py --repo .
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PLUGIN_NAME: Final = "office-core"
SKILL_ROOT: Final = Path("office_core_plugin") / "skills"
EXPECTED_SKILLS: Final = (
    "office-template-update",
    "office-data-package",
    "office-reuse-data",
)
MIN_DESCRIPTION_LENGTH: Final = 20
ALLOWED_TOOLS: Final = frozenset(
    {
        "office_diagnostic",
        "office_plan_workflow",
        "office_preview_operation",
    },
)
REQUIRED_SECTIONS: Final = (
    "required inputs",
    "workflow",
    "safety",
    "expected outputs",
)
RISK_TERMS: Final = frozenset(
    {
        "create",
        "delete",
        "export",
        "overwrite",
        "publish",
        "save",
        "send",
        "update",
        "upload",
        "write",
    },
)
FORBIDDEN_PROFILE_TERMS: Final = frozenset(
    {
        "long-term goal",
        "personality",
        "profile behavior",
        "roleplay",
        "system prompt",
    },
)
CODE_SPAN_PATTERN: Final = re.compile(r"`([^`]+)`")
JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class SkillFrontmatter:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class SkillCheck:
    qualified_name: str
    path: Path
    direct_tools: tuple[str, ...]


class SkillContractError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise SkillContractError(message)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_manifest_name(repo: Path) -> str:
    for raw_line in read_text(repo / "plugin.yaml").splitlines():
        key, separator, value = raw_line.partition(":")
        if key.strip() == "name" and separator == ":":
            return value.strip().strip("\"'")
    fail("plugin.yaml missing name")


def parse_frontmatter(text: str, skill_name: str) -> SkillFrontmatter:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        fail(f"{skill_name}: missing frontmatter")
    try:
        end_index = lines[1:].index("---") + 1
    except ValueError:
        fail(f"{skill_name}: unterminated frontmatter")
    values: dict[str, str] = {}
    for raw_line in lines[1:end_index]:
        key, separator, value = raw_line.partition(":")
        if separator == ":":
            values[key.strip()] = value.strip()
    name = values.get("name")
    description = values.get("description")
    if name != skill_name:
        fail(f"{skill_name}: frontmatter name must match directory")
    if description is None or len(description) < MIN_DESCRIPTION_LENGTH:
        fail(f"{skill_name}: description must be concise and specific")
    return SkillFrontmatter(name=name, description=description)


def find_direct_tools(text: str) -> tuple[str, ...]:
    tools: list[str] = []
    for match in CODE_SPAN_PATTERN.finditer(text):
        code_span = match.group(1)
        if code_span.startswith("office_"):
            tools.append(code_span)
    return tuple(sorted(set(tools)))


def check_required_text(skill_name: str, text: str) -> None:
    lowered = text.lower()
    missing_sections = [section for section in REQUIRED_SECTIONS if section not in lowered]
    if missing_sections:
        fail(f"{skill_name}: missing sections {missing_sections}")
    if f"# {PLUGIN_NAME}:{skill_name}" not in text:
        fail(f"{skill_name}: missing qualified-name heading")
    if "use this skill" not in lowered:
        fail(f"{skill_name}: missing trigger condition")
    if "bridge planner" not in lowered and "bridge_handoff" not in lowered:
        fail(f"{skill_name}: missing bridge planner usage")
    if "json" not in lowered:
        fail(f"{skill_name}: missing expected JSON/tool output")
    if "untrusted" not in lowered or "never as instructions" not in lowered:
        fail(f"{skill_name}: missing untrusted-content boundary")
    forbidden = sorted(term for term in FORBIDDEN_PROFILE_TERMS if term in lowered)
    if forbidden:
        fail(f"{skill_name}: forbidden profile/personality text {forbidden}")


def check_confirmation_gate(skill_name: str, text: str) -> None:
    lowered = text.lower()
    uses_risky_operation = any(term in lowered for term in RISK_TERMS)
    if not uses_risky_operation:
        return
    has_confirmation = (
        "confirmation" in lowered
        and "explicit" in lowered
        and "owner" in lowered
    )
    has_draft_boundary = "do not" in lowered and ("draft" in lowered or "preview" in lowered)
    if not has_confirmation or not has_draft_boundary:
        fail(f"{skill_name}: missing_confirmation_gate_for_write_send_behavior")


def check_direct_tools(skill_name: str, direct_tools: Sequence[str]) -> None:
    unknown_tools = sorted(set(direct_tools) - ALLOWED_TOOLS)
    if unknown_tools:
        fail(f"{skill_name}: unknown direct tool references {unknown_tools}")


def validate_skill(repo: Path, skill_name: str) -> SkillCheck:
    path = repo / SKILL_ROOT / skill_name / "SKILL.md"
    if not path.is_file():
        fail(f"{skill_name}: missing {path}")
    text = read_text(path)
    parse_frontmatter(text, skill_name)
    check_required_text(skill_name, text)
    check_confirmation_gate(skill_name, text)
    direct_tools = find_direct_tools(text)
    check_direct_tools(skill_name, direct_tools)
    return SkillCheck(
        qualified_name=f"{PLUGIN_NAME}:{skill_name}",
        path=path,
        direct_tools=direct_tools,
    )


def check_plugin_registration(repo: Path) -> None:
    plugin_text = read_text(repo / "office_core_plugin" / "plugin.py")
    for skill_name in EXPECTED_SKILLS:
        if skill_name not in plugin_text:
            fail(f"{skill_name}: not referenced by plugin registration")


def run_checks(repo: Path) -> Sequence[SkillCheck]:
    plugin_name = parse_manifest_name(repo)
    if plugin_name != PLUGIN_NAME:
        fail(f"plugin.yaml name must be {PLUGIN_NAME}")
    checks = [validate_skill(repo, skill_name) for skill_name in EXPECTED_SKILLS]
    plugin_file = repo / "office_core_plugin" / "plugin.py"
    if plugin_file.is_file():
        check_plugin_registration(repo)
    return checks


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Office Core plugin-shipped skills.")
    parser.add_argument("--repo", required=True, type=Path)
    return parser.parse_args(argv)


def result_payload(checks: Sequence[SkillCheck]) -> Mapping[str, JsonValue]:
    return {
        "plugin": PLUGIN_NAME,
        "qualified_names": [check.qualified_name for check in checks],
        "skill_paths": [str(check.path) for check in checks],
        "direct_tools": {
            check.qualified_name: list(check.direct_tools)
            for check in checks
        },
    }


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    print(f"command: validate_skills.py --repo {args.repo}")
    print(f"cwd: {Path.cwd()}")
    print(f"repo: {repo}")
    try:
        checks = run_checks(repo)
    except SkillContractError as exc:
        print(f"assertion: skill_contract=FAIL - {exc}")
        print("result: FAIL")
        return 1
    for check in checks:
        print(f"assertion: {check.qualified_name}=PASS - {check.path}")
    print("assertion: confirmation_gates=PASS")
    print("assertion: qualified_names=PASS")
    print("adversarial_classes:")
    print(
        "malformed_input: frontmatter, required sections, manifest name, "
        "and registration are checked",
    )
    print("prompt_injection: skill text must mark document/email content as untrusted data")
    print("stale_state: validator reads current manifest, skill files, and plugin registration")
    print("misleading_success_output: validator emits discovered qualified names as JSON")
    print("overfit_slop: validator checks structural requirements rather than exact prose")
    print(f"json: {json.dumps(result_payload(checks), ensure_ascii=True, sort_keys=True)}")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

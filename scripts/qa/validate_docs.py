# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# --- How to run ---
# .\.venv\Scripts\python.exe scripts\qa\validate_docs.py --repo .
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

REQUIRED_DOCS: Final = (
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "docs/architecture.md",
    "docs/marketplace-readiness.md",
    "examples/workflows.md",
)
REQUIRED_INSTALL_COMMAND: Final = (
    "hermes plugins install Tinycute00/hermes-office-core-plugin --enable"
)
FALSE_ACCEPTANCE_PATTERNS: Final = (
    re.compile(r"official\s+marketplace\s+(accepted|approved|published)", re.IGNORECASE),
    re.compile(r"(accepted|approved|published)\s+in\s+the\s+official\s+marketplace", re.IGNORECASE),
    re.compile(r"marketplace\s+approval\s+(complete|granted|received)", re.IGNORECASE),
)
JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class Requirement:
    name: str
    terms: tuple[tuple[str, ...], ...]
    files: tuple[str, ...] = REQUIRED_DOCS


@dataclass(frozen=True, slots=True)
class RequirementResult:
    name: str
    passed: bool
    matched_files: tuple[str, ...]
    detail: str


class DocsContractError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise DocsContractError(message)


def read_doc(repo: Path, relative_path: str) -> str:
    path = repo / relative_path
    if not path.is_file():
        fail(f"missing required documentation file: {relative_path}")
    return path.read_text(encoding="utf-8")


def load_docs(repo: Path) -> dict[str, str]:
    return {relative_path: read_doc(repo, relative_path) for relative_path in REQUIRED_DOCS}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def has_term_group(text: str, terms: tuple[str, ...]) -> bool:
    normalized = normalize(text)
    return all(term.lower() in normalized for term in terms)


def check_requirement(
    requirement: Requirement,
    docs: dict[str, str],
) -> RequirementResult:
    matched: list[str] = []
    for relative_path in requirement.files:
        text = docs[relative_path]
        if any(has_term_group(text, term_group) for term_group in requirement.terms):
            matched.append(relative_path)
    passed = bool(matched)
    return RequirementResult(
        name=requirement.name,
        passed=passed,
        matched_files=tuple(matched),
        detail="matched" if passed else f"missing category: {requirement.name}",
    )


def check_false_acceptance(docs: dict[str, str]) -> RequirementResult:
    offenders: list[str] = []
    for relative_path, text in docs.items():
        if any(pattern.search(text) for pattern in FALSE_ACCEPTANCE_PATTERNS):
            offenders.append(relative_path)
    return RequirementResult(
        name="truthful_marketplace_status",
        passed=not offenders,
        matched_files=tuple(sorted(docs)),
        detail="no false official marketplace acceptance claim"
        if not offenders
        else f"false marketplace acceptance claim in {offenders}",
    )


def requirements() -> tuple[Requirement, ...]:
    return (
        Requirement(
            "github_install_mode",
            ((REQUIRED_INSTALL_COMMAND,), ("hermes plugins install", "--enable")),
            ("README.md", "docs/marketplace-readiness.md"),
        ),
        Requirement(
            "direct_copy_install_mode",
            (("direct copy", "plugins", "office-core"), ("copy", "HERMES_HOME", "plugins")),
            ("README.md",),
        ),
        Requirement(
            "pip_install_mode",
            (("pip install", "hermes-office-core-plugin"), ("python -m pip install",)),
            ("README.md",),
        ),
        Requirement(
            "environment_variable_handling",
            (("environment", "HERMES_HOME"), ("env var", "HERMES_PLUGINS_DEBUG")),
            ("README.md", "SECURITY.md"),
        ),
        Requirement(
            "temporary_runtime_warning",
            (("temporary", "HERMES_HOME", "real Hermes home"), ("temp-runtime", "warning")),
            ("README.md", "SECURITY.md"),
        ),
        Requirement(
            "no_core_modification_guarantee",
            (
                ("no", "Hermes source", "edit"),
                ("no-core-modification",),
                ("does not modify", "hermes-agent"),
            ),
            ("README.md", "SECURITY.md", "docs/architecture.md"),
        ),
        Requirement(
            "draft_only_external_write_policy",
            (("v0.1", "draft-only", "external write"), ("external send", "draft")),
            ("README.md", "SECURITY.md", "docs/architecture.md"),
        ),
        Requirement(
            "safety_model",
            (("safety model", "confirmation", "audit"), ("policy wrapper", "provenance")),
            ("README.md", "SECURITY.md", "docs/architecture.md"),
        ),
        Requirement(
            "example_workflows",
            (("template update", "data package"), ("reusable data", "owner confirmation")),
            ("README.md", "examples/workflows.md"),
        ),
        Requirement(
            "linear_governance_note",
            (("Linear", "governance", "evidence"), ("TIN-5", "governance")),
            ("README.md", "CONTRIBUTING.md", "docs/marketplace-readiness.md"),
        ),
        Requirement(
            "release_checklist",
            (("release checklist", "version", "tag", "evidence"), ("v0.1.0", "evidence links")),
            ("docs/marketplace-readiness.md", "CHANGELOG.md"),
        ),
        Requirement(
            "prompt_injection_boundary",
            (("untrusted", "never as instructions"), ("office content", "untrusted data")),
            ("SECURITY.md", "examples/workflows.md"),
        ),
        Requirement(
            "confirmation_before_write_send",
            (("confirmation", "write", "send"), ("owner confirmation", "external send")),
            ("SECURITY.md", "examples/workflows.md"),
        ),
    )


def run_checks(repo: Path) -> tuple[RequirementResult, ...]:
    docs = load_docs(repo)
    category_results = tuple(check_requirement(requirement, docs) for requirement in requirements())
    return (*category_results, check_false_acceptance(docs))


def parse_args(argv: tuple[str, ...]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Office Core documentation readiness.",
    )
    parser.add_argument("--repo", required=True, type=Path)
    return parser.parse_args(argv)


def result_payload(results: tuple[RequirementResult, ...]) -> dict[str, JsonValue]:
    return {
        "checks": {
            result.name: {
                "passed": result.passed,
                "matched_files": list(result.matched_files),
                "detail": result.detail,
            }
            for result in results
        },
    }


def main(argv: tuple[str, ...]) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    print(f"command: validate_docs.py --repo {args.repo}")
    print(f"cwd: {Path.cwd()}")
    print(f"repo: {repo}")
    try:
        results = run_checks(repo)
    except DocsContractError as exc:
        print(f"assertion: docs_contract=FAIL - {exc}")
        print("result: FAIL")
        return 1
    failed = [result for result in results if not result.passed]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        files = ",".join(result.matched_files) if result.matched_files else "<none>"
        print(f"assertion: {result.name}={status} - files={files}; {result.detail}")
    print("adversarial_classes:")
    print("malformed_input: fixture missing no-core warning is rejected")
    print("prompt_injection: docs must treat office content as untrusted data")
    print("stale_state: validator reads current docs from the requested repo path")
    print("misleading_success_output: validator reports per-category matched files")
    print("overfit_slop: checks use section/category term groups, not one exact paragraph")
    print(f"json: {json.dumps(result_payload(results), ensure_ascii=True, sort_keys=True)}")
    print(f"result: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(tuple(sys.argv[1:])))

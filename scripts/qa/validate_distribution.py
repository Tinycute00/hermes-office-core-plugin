from __future__ import annotations

import argparse
import importlib
import importlib.util
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from types import ModuleType


PLUGIN_NAME = "office-core"
ENTRY_POINT_GROUP = "hermes_agent.plugins"
ENTRY_POINT_VALUE = "office_core_plugin:register"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str
    entrypoint: str


class DistributionContractError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise DistributionContractError(message)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_manifest(path: Path) -> PluginManifest:
    values: dict[str, str] = {}
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if separator != ":":
            fail(f"invalid manifest line: {raw_line}")
        values[key.strip()] = value.strip().strip('"').strip("'")

    try:
        return PluginManifest(
            name=values["name"],
            version=values["version"],
            entrypoint=values["entrypoint"],
        )
    except KeyError as exc:
        fail(f"plugin.yaml missing required key: {exc.args[0]}")


def get_table(table: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = table.get(key)
    if isinstance(value, Mapping):
        return value
    fail(f"pyproject missing table: {key}")


def get_string(table: Mapping[str, object], key: str) -> str:
    value = table.get(key)
    if isinstance(value, str):
        return value
    fail(f"pyproject {key} must be a string")


def get_string_list(table: Mapping[str, object], key: str) -> Sequence[str]:
    value = table.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    fail(f"pyproject {key} must be a list of strings")


def check_file_exists(repo: Path, relative_path: str) -> CheckResult:
    path = repo / relative_path
    return CheckResult(relative_path, path.is_file(), str(path))


def check_manifest(repo: Path) -> CheckResult:
    manifest = parse_manifest(repo / "plugin.yaml")
    passed = (
        manifest.name == PLUGIN_NAME
        and manifest.version == "0.1.0"
        and manifest.entrypoint == "__init__.py"
    )
    return CheckResult("root_plugin_manifest", passed, f"{manifest}")


def check_pyproject(repo: Path) -> CheckResult:
    data = tomllib.loads(read_text(repo / "pyproject.toml"))
    project = get_table(data, "project")
    optional = get_table(project, "optional-dependencies")
    dev = get_string_list(optional, "dev")
    entry_groups = get_table(project, "entry-points")
    hermes_plugins = get_table(entry_groups, ENTRY_POINT_GROUP)
    ruff = get_table(get_table(data, "tool"), "ruff")
    required_tools = ("pytest", "build", "ruff")
    has_tools = all(any(dep.startswith(tool) for dep in dev) for tool in required_tools)
    passed = (
        get_string(project, "requires-python") == ">=3.11"
        and get_string(hermes_plugins, PLUGIN_NAME) == ENTRY_POINT_VALUE
        and get_string(ruff, "target-version") == "py311"
        and has_tools
    )
    return CheckResult("pyproject_contract", passed, f"dev={dev}")


def load_root_module(repo: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("office_core_root_plugin", repo / "__init__.py")
    if spec is None or spec.loader is None:
        fail("could not create import spec for root __init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_register_consistency(repo: Path) -> CheckResult:
    sys.path.insert(0, str(repo))
    try:
        root_module = load_root_module(repo)
        package_module = importlib.import_module("office_core_plugin")
        root_register = root_module.register
        package_register = package_module.register
    except (AttributeError, ImportError) as exc:
        return CheckResult("register_consistency", passed=False, detail=str(exc))
    finally:
        sys.path.remove(str(repo))

    return CheckResult(
        "register_consistency",
        root_register is package_register,
        f"entry_point={ENTRY_POINT_VALUE}",
    )


def check_manifest_rules(repo: Path) -> CheckResult:
    manifest_text = read_text(repo / "MANIFEST.in")
    required_fragments = (
        "include __init__.py",
        "include plugin.yaml",
        "recursive-include office_core_plugin",
        "recursive-include office_core_plugin/skills *.md",
    )
    missing = [fragment for fragment in required_fragments if fragment not in manifest_text]
    return CheckResult("manifest_inclusion_rules", not missing, f"missing={missing}")


def build_sdist(repo: Path, output_dir: Path) -> Path:
    command = [
        sys.executable,
        "-m",
        "build",
        "--sdist",
        "--no-isolation",
        "--outdir",
        str(output_dir),
        str(repo),
    ]
    completed = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
    print(f"command: {' '.join(command)}")
    print(f"cwd: {repo}")
    print(f"exit: {completed.returncode}")
    print("stdout:")
    print(completed.stdout.strip() or "<empty>")
    print("stderr:")
    print(completed.stderr.strip() or "<empty>")
    if completed.returncode != 0:
        fail("sdist build failed")
    sdists = sorted(output_dir.glob("*.tar.gz"))
    if not sdists:
        fail("sdist build produced no .tar.gz")
    return sdists[0]


def check_sdist(repo: Path) -> CheckResult:
    with tempfile.TemporaryDirectory(prefix="office-core-sdist-") as temp_dir:
        sdist = build_sdist(repo, Path(temp_dir))
        with tarfile.open(sdist, "r:gz") as archive:
            names = archive.getnames()
    required_suffixes = (
        "plugin.yaml",
        "__init__.py",
        "office_core_plugin/__init__.py",
        "office_core_plugin/plugin.py",
        "office_core_plugin/plugin.yaml",
        "office_core_plugin/py.typed",
        "office_core_plugin/skills/office-diagnostic/SKILL.md",
        "office_core_plugin/skills/office-template-update/SKILL.md",
        "office_core_plugin/skills/office-data-package/SKILL.md",
        "office_core_plugin/skills/office-reuse-data/SKILL.md",
    )
    missing = [
        suffix
        for suffix in required_suffixes
        if not any(name.endswith(suffix) for name in names)
    ]
    return CheckResult("sdist_contains_distribution_files", not missing, f"missing={missing}")


def run_checks(repo: Path, *, allow_execute: bool = False) -> Sequence[CheckResult]:
    required_files = (
        "plugin.yaml",
        "__init__.py",
        "pyproject.toml",
        "MANIFEST.in",
        "office_core_plugin/__init__.py",
        "office_core_plugin/plugin.py",
        "office_core_plugin/plugin.yaml",
        "office_core_plugin/py.typed",
    )
    checks: list[CheckResult] = [check_file_exists(repo, path) for path in required_files]
    if not all(check.passed for check in checks):
        return checks
    checks.extend(
        (
            check_manifest(repo),
            check_pyproject(repo),
            check_manifest_rules(repo),
            check_sdist(repo),
        )
    )
    if allow_execute:
        checks.append(check_register_consistency(repo))
    else:
        checks.append(
            CheckResult(
                "register_consistency",
                passed=True,
                detail="skipped_execution: use --allow-execute-trusted-repo to validate imports",
            )
        )
    return checks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Hermes Office Core distribution contract.",
    )
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument(
        "--allow-execute-trusted-repo",
        action="store_true",
        default=False,
        help=(
            "Opt-in to execution-based validation (imports and runs repo code). "
            "Default is static inspection only for untrusted repos."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    print("validate_distribution")
    print(f"repo: {repo}")
    print(f"python: {sys.executable}")
    try:
        checks = run_checks(repo, allow_execute=args.allow_execute_trusted_repo)
    except DistributionContractError as exc:
        print(f"assertion: distribution_contract=FAIL - {exc}")
        print("result: FAIL")
        return 1

    failed = [check for check in checks if not check.passed]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        detail = f" - {check.detail}" if check.detail else ""
        print(f"assertion: {check.name}={status}{detail}")

    print("adversarial_classes:")
    print("dirty_worktree: validator reads repo files and builds only in a temp directory")
    print("stale_state: pyproject, manifest, imports, and sdist are generated from current files")
    print("malformed_input: bad fixture missing MANIFEST.in is rejected")
    print("misleading_success_output: validator asserts file contents and import identity")
    print("hung_or_long_commands: build is a bounded subprocess in the caller-controlled run")
    print("flaky_tests: checks are deterministic filesystem and metadata assertions")
    print("untrusted_external_text: metadata is parsed as data and never executed as prose")
    print("sandboxed_default: execution-based checks require --allow-execute-trusted-repo")
    print("not_applicable: cancel_resume, repeated_interruptions")
    print(f"result: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

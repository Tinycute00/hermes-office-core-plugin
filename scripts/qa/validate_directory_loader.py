#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. With uv:
#      uv run scripts/qa/validate_directory_loader.py --repo .
# 2. With project Python:
#      python scripts/qa/validate_directory_loader.py --repo .
# ──────────────────

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn

if TYPE_CHECKING:
    from collections.abc import Sequence

SPLIT_REPO_ARG_COUNT: Final = 3
INLINE_REPO_ARG_COUNT: Final = 2


class DirectoryLoaderValidationError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise DirectoryLoaderValidationError(message)


def run_static_checks(repo: Path) -> list[dict[str, object]]:
    """Static inspection checks that do not execute repo code."""
    checks: list[dict[str, object]] = []

    required_files = (
        "__init__.py",
        "plugin.yaml",
        "pyproject.toml",
        "office_core_plugin/__init__.py",
        "office_core_plugin/plugin.py",
    )
    for rel_path in required_files:
        path = repo / rel_path
        checks.append(
            {
                "name": f"file_exists:{rel_path}",
                "passed": path.is_file(),
                "detail": str(path),
            }
        )

    # Parse plugin.yaml without executing it.
    manifest_path = repo / "plugin.yaml"
    if manifest_path.is_file():
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest_ok = (
            "name: office-core" in manifest_text
            and "version: 0.1.0" in manifest_text
            and "entrypoint: __init__.py" in manifest_text
        )
        checks.append(
            {
                "name": "manifest_content",
                "passed": manifest_ok,
                "detail": "plugin.yaml static inspection",
            }
        )
    else:
        checks.append(
            {
                "name": "manifest_content",
                "passed": False,
                "detail": "plugin.yaml missing",
            }
        )

    # Parse pyproject.toml entry point without executing.
    pyproject_path = repo / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            project = data.get("project", {})
            entry_groups = project.get("entry-points", {})
            hermes_plugins = entry_groups.get("hermes_agent.plugins", {})
            entry_ok = hermes_plugins.get("office-core") == "office_core_plugin:register"
            checks.append(
                {
                    "name": "pyproject_entry_point",
                    "passed": entry_ok,
                    "detail": "pyproject.toml static inspection",
                }
            )
        except (AttributeError, tomllib.TOMLDecodeError) as exc:
            checks.append(
                {
                    "name": "pyproject_entry_point",
                    "passed": False,
                    "detail": f"parse error: {exc}",
                }
            )
    else:
        checks.append(
            {
                "name": "pyproject_entry_point",
                "passed": False,
                "detail": "pyproject.toml missing",
            }
        )

    return checks

def run_strict_loader(repo: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    _ = env.pop("PYTHONPATH", None)
    child_script = Path(__file__).with_name("directory_loader_child.py")
    with tempfile.TemporaryDirectory(prefix="office-core-loader-") as temp_dir:
        return subprocess.run(
            [sys.executable, str(child_script), str(repo)],
            cwd=temp_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )


def parse_repo_arg(argv: Sequence[str]) -> tuple[Path, bool]:
    parser = argparse.ArgumentParser(
        description="Validate the Hermes Office Core directory loader contract.",
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
    args = parser.parse_args(argv[1:])
    return args.repo.resolve(strict=True), args.allow_execute_trusted_repo


def main(argv: Sequence[str] = sys.argv) -> int:
    repo, allow_execute = parse_repo_arg(argv)
    static_checks = run_static_checks(repo)
    for check in static_checks:
        status = "PASS" if check["passed"] else "FAIL"
        detail = f" - {check['detail']}" if check["detail"] else ""
        print(f"assertion: {check['name']}={status}{detail}")

    if not all(check["passed"] for check in static_checks):
        print("result: FAIL")
        return 1

    if not allow_execute:
        print("assertion: execution_checks=SKIP - use --allow-execute-trusted-repo to run")
        print("adversarial_classes:")
        print("sandboxed_default: execution-based checks require --allow-execute-trusted-repo")
        print("result: PASS")
        return 0

    completed = run_strict_loader(repo)
    print(f"command=<{sys.executable} directory_loader_child.py {repo}>")
    print(f"exit_code={completed.returncode}")
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print("stderr:")
        print(completed.stderr.rstrip())
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

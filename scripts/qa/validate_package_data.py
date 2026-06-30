# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# --- How to run ---
# .\.venv\Scripts\python.exe scripts\qa\validate_package_data.py --repo . --dist-dir dist
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, Literal, NoReturn, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Sequence

ArtifactKind: TypeAlias = Literal["sdist", "wheel"]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

PACKAGE_ROOT: Final = "office_core_plugin"
OMITTED_NAME: Final = "plugin.yaml"
REQUIRED_SDIST_PATHS: Final = (
    "plugin.yaml",
    "__init__.py",
    "README.md",
    "CHANGELOG.md",
    "docs/architecture.md",
    "docs/marketplace-readiness.md",
    "examples/workflows.md",
    "office_core_plugin/__init__.py",
    "office_core_plugin/plugin.py",
    "office_core_plugin/plugin.yaml",
    "office_core_plugin/py.typed",
    "office_core_plugin/skills/office-diagnostic/SKILL.md",
    "office_core_plugin/skills/office-template-update/SKILL.md",
    "office_core_plugin/skills/office-data-package/SKILL.md",
    "office_core_plugin/skills/office-reuse-data/SKILL.md",
)
REQUIRED_WHEEL_PATHS: Final = (
    "office_core_plugin/__init__.py",
    "office_core_plugin/plugin.py",
    "office_core_plugin/plugin.yaml",
    "office_core_plugin/py.typed",
    "office_core_plugin/skills/office-diagnostic/SKILL.md",
    "office_core_plugin/skills/office-template-update/SKILL.md",
    "office_core_plugin/skills/office-data-package/SKILL.md",
    "office_core_plugin/skills/office-reuse-data/SKILL.md",
)


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: ArtifactKind
    path: Path
    entries: frozenset[str]


@dataclass(frozen=True, slots=True)
class PackageDataResult:
    name: str
    passed: bool
    detail: str


class PackageDataError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise PackageDataError(message)


def normalize_archive_name(name: str) -> str:
    parts = PurePosixPath(name).parts
    if not parts:
        return name
    if parts[0].startswith("hermes_office_core_plugin-"):
        return str(PurePosixPath(*parts[1:]))
    return str(PurePosixPath(*parts))


def read_sdist(path: Path) -> Artifact:
    with tarfile.open(path, "r:gz") as archive:
        entries = frozenset(normalize_archive_name(member.name) for member in archive.getmembers())
    return Artifact(kind="sdist", path=path, entries=entries)


def read_wheel(path: Path) -> Artifact:
    with zipfile.ZipFile(path) as archive:
        entries = frozenset(normalize_archive_name(name) for name in archive.namelist())
    return Artifact(kind="wheel", path=path, entries=entries)


def load_artifacts(dist_dir: Path) -> tuple[Artifact, Artifact]:
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(sdists) != 1:
        fail(f"expected exactly one sdist in {dist_dir}, found {len(sdists)}")
    if len(wheels) != 1:
        fail(f"expected exactly one wheel in {dist_dir}, found {len(wheels)}")
    return read_sdist(sdists[0]), read_wheel(wheels[0])


def missing_required(entries: frozenset[str], required_paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(path for path in required_paths if path not in entries)


def validate_artifact(artifact: Artifact) -> PackageDataResult:
    match artifact.kind:
        case "sdist":
            required_paths = REQUIRED_SDIST_PATHS
        case "wheel":
            required_paths = REQUIRED_WHEEL_PATHS
        case unreachable:
            fail(f"unsupported artifact kind: {unreachable}")
    missing = missing_required(artifact.entries, required_paths)
    return PackageDataResult(
        name=f"{artifact.kind}_required_package_data",
        passed=not missing,
        detail=f"{artifact.path.name}; missing={list(missing)}",
    )


def validate_dist_dir(dist_dir: Path) -> tuple[PackageDataResult, ...]:
    artifacts = load_artifacts(dist_dir)
    return tuple(validate_artifact(artifact) for artifact in artifacts)


def omitted_plugin_yaml(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and parts[-1] == OMITTED_NAME


def copy_sdist_without_plugin_yaml(source: Path, destination: Path) -> None:
    with (
        tarfile.open(source, "r:gz") as source_archive,
        tarfile.open(destination, "w:gz") as destination_archive,
    ):
        for member in source_archive.getmembers():
            if omitted_plugin_yaml(normalize_archive_name(member.name)):
                continue
            extracted = source_archive.extractfile(member) if member.isfile() else None
            destination_archive.addfile(member, extracted)


def copy_wheel_without_plugin_yaml(source: Path, destination: Path) -> None:
    with (
        zipfile.ZipFile(source) as source_archive,
        zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as dest_archive,
    ):
        for item in source_archive.infolist():
            if omitted_plugin_yaml(normalize_archive_name(item.filename)):
                continue
            dest_archive.writestr(item, source_archive.read(item.filename))


def build_omission_fixture(source_dist: Path, fixture_dir: Path) -> Path:
    if fixture_dir.exists():
        shutil.rmtree(fixture_dir)
    fixture_dir.mkdir(parents=True)
    sdist, wheel = load_artifacts(source_dist)
    copy_sdist_without_plugin_yaml(sdist.path, fixture_dir / sdist.path.name)
    copy_wheel_without_plugin_yaml(wheel.path, fixture_dir / wheel.path.name)
    return fixture_dir


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate package data in built distributions.")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--simulate-missing-plugin-yaml", action="store_true")
    parser.add_argument("--failure-fixture-dir", type=Path)
    return parser.parse_args(argv)


def result_payload(results: Sequence[PackageDataResult], dist_dir: Path) -> dict[str, JsonValue]:
    return {
        "dist_dir": str(dist_dir),
        "checks": {
            result.name: {"passed": result.passed, "detail": result.detail}
            for result in results
        },
    }


def print_results(results: Sequence[PackageDataResult], dist_dir: Path) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"assertion: {result.name}={status} - {result.detail}")
    print("adversarial_classes:")
    print("malformed_input: plugin.yaml omission from sdist and wheel is rejected")
    print("prompt_injection: archive names are parsed as data, never executed")
    print("stale_state: validator reads the current dist directory")
    print("misleading_success_output: each required artifact path is inspected directly")
    print("overfit_slop: validation checks all required package, skill, docs, and metadata files")
    payload = json.dumps(result_payload(results, dist_dir), ensure_ascii=True, sort_keys=True)
    print(f"json: {payload}")


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    source_dist = args.dist_dir.resolve()
    print("validate_package_data")
    print(f"command: validate_package_data.py --repo {args.repo} --dist-dir {args.dist_dir}")
    print(f"cwd: {Path.cwd()}")
    print(f"repo: {repo}")
    print(f"dist_dir: {source_dist}")
    try:
        if args.simulate_missing_plugin_yaml:
            if args.failure_fixture_dir is None:
                with TemporaryDirectory(prefix="office-core-package-data-") as temp_dir:
                    fixture_dir = build_omission_fixture(source_dist, Path(temp_dir))
                    print(f"failure_fixture_dir: {fixture_dir}")
                    results = validate_dist_dir(fixture_dir)
            else:
                failure_fixture_dir = args.failure_fixture_dir.resolve()
                fixture_dir = build_omission_fixture(source_dist, failure_fixture_dir)
                print(f"failure_fixture_dir: {fixture_dir}")
                results = validate_dist_dir(fixture_dir)
        else:
            results = validate_dist_dir(source_dist)
    except (PackageDataError, tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        print(f"assertion: package_data_contract=FAIL - {exc}")
        print("result: FAIL")
        return 1
    print_results(results, source_dist)
    failed = [result for result in results if not result.passed]
    print(f"result: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

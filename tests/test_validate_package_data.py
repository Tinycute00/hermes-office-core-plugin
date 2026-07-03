import subprocess
import sys
from pathlib import Path

import pytest

from scripts.qa import validate_package_data


def test_validate_package_data_rejects_missing_plugin_yaml_in_artifacts(tmp_path: Path) -> None:
    # Given: built artifact entries with every required path except plugin.yaml files.
    sdist = validate_package_data.Artifact(
        kind="sdist",
        path=tmp_path / "broken.tar.gz",
        entries=frozenset(
            path
            for path in validate_package_data.REQUIRED_SDIST_PATHS
            if not path.endswith("plugin.yaml")
        ),
    )
    wheel = validate_package_data.Artifact(
        kind="wheel",
        path=tmp_path / "broken.whl",
        entries=frozenset(
            path
            for path in validate_package_data.REQUIRED_WHEEL_PATHS
            if not path.endswith("plugin.yaml")
        ),
    )

    # When: package-data validation inspects both artifacts.
    results = (
        validate_package_data.validate_artifact(sdist),
        validate_package_data.validate_artifact(wheel),
    )

    # Then: both artifacts fail for omitted plugin.yaml package data.
    assert results[0].passed is False
    assert "plugin.yaml" in results[0].detail
    assert results[1].passed is False
    assert "office_core_plugin/plugin.yaml" in results[1].detail


def test_validate_package_data_accepts_required_artifact_entries(tmp_path: Path) -> None:
    # Given: built artifact entries containing every required package, skill, and docs asset.
    sdist = validate_package_data.Artifact(
        kind="sdist",
        path=tmp_path / "ok.tar.gz",
        entries=frozenset(validate_package_data.REQUIRED_SDIST_PATHS),
    )
    wheel = validate_package_data.Artifact(
        kind="wheel",
        path=tmp_path / "ok.whl",
        entries=frozenset(validate_package_data.REQUIRED_WHEEL_PATHS),
    )

    # When: package-data validation inspects the required entries.
    results = (
        validate_package_data.validate_artifact(sdist),
        validate_package_data.validate_artifact(wheel),
    )

    # Then: both artifact contracts pass.
    assert results[0].passed is True
    assert results[1].passed is True


def test_validate_package_data_rejects_missing_built_artifact(tmp_path: Path) -> None:
    # Given: an empty dist directory.
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    # When / Then: validation fails instead of inferring success from missing output.
    with pytest.raises(validate_package_data.PackageDataError, match="expected exactly one sdist"):
        validate_package_data.validate_dist_dir(dist_dir)


def test_validate_distribution_defaults_to_static_inspection_without_execution(
    tmp_path: Path,
) -> None:
    # Given: a minimal repo structure that passes static checks but has a malicious __init__.py.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "plugin.yaml").write_text(
        "name: office-core\nversion: 0.1.0\nentrypoint: __init__.py\n",
        encoding="utf-8",
    )
    (repo / "__init__.py").write_text(
        "import os\nos.system('echo MALICIOUS')\n\ndef register(ctx):\n    pass\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "hermes-office-core-plugin"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        'optional-dependencies = { dev = ["pytest", "build", "ruff"] }\n'
        '[project.entry-points."hermes_agent.plugins"]\n'
        'office-core = "office_core_plugin:register"\n'
        '[tool.ruff]\ntarget-version = "py311"\n',
        encoding="utf-8",
    )
    (repo / "MANIFEST.in").write_text(
        "include __init__.py\ninclude plugin.yaml\n"
        "recursive-include office_core_plugin *\n"
        "recursive-include office_core_plugin/skills *.md\n",
        encoding="utf-8",
    )
    (repo / "office_core_plugin").mkdir()
    (repo / "office_core_plugin" / "__init__.py").write_text(
        "def register(ctx): pass\n",
        encoding="utf-8",
    )
    (repo / "office_core_plugin" / "plugin.py").write_text("# plugin\n", encoding="utf-8")
    (repo / "office_core_plugin" / "plugin.yaml").write_text(
        "name: office-core\n",
        encoding="utf-8",
    )
    (repo / "office_core_plugin" / "py.typed").write_text("", encoding="utf-8")
    (repo / "office_core_plugin" / "skills" / "office-diagnostic").mkdir(parents=True)
    (repo / "office_core_plugin" / "skills" / "office-diagnostic" / "SKILL.md").write_text(
        "# skill\n",
        encoding="utf-8",
    )
    (repo / "office_core_plugin" / "skills" / "office-template-update").mkdir(parents=True)
    (
        repo / "office_core_plugin" / "skills" / "office-template-update" / "SKILL.md"
    ).write_text("# skill\n", encoding="utf-8")
    (repo / "office_core_plugin" / "skills" / "office-data-package").mkdir(parents=True)
    (repo / "office_core_plugin" / "skills" / "office-data-package" / "SKILL.md").write_text(
        "# skill\n",
        encoding="utf-8",
    )
    (repo / "office_core_plugin" / "skills" / "office-reuse-data").mkdir(parents=True)
    (repo / "office_core_plugin" / "skills" / "office-reuse-data" / "SKILL.md").write_text(
        "# skill\n",
        encoding="utf-8",
    )

    # When: validate_distribution is run WITHOUT the execution flag.
    script = Path(__file__).resolve().parents[1] / "scripts" / "qa" / "validate_distribution.py"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(script),
            "--repo",
            str(repo),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    # Then: static checks pass, execution is skipped, and malicious code is never run.
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "register_consistency=PASS" in result.stdout
    assert "skipped_execution" in result.stdout
    assert "sandboxed_default" in result.stdout
    assert "MALICIOUS" not in result.stdout

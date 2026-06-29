from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.qa import validate_package_data

if TYPE_CHECKING:
    from pathlib import Path


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

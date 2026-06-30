from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from tests.register_contract_helpers import EXPECTED_SKILLS, EXPECTED_TOOLS

if TYPE_CHECKING:
    from collections.abc import Mapping

HERMES_OFFICIAL_SOURCE_ENV: Final = "HERMES_OFFICIAL_SOURCE"
DEFAULT_HERMES_SOURCE: Final = Path(r"C:\Users\88697\AppData\Local\hermes\hermes-agent")


@dataclass(frozen=True, slots=True)
class OfficialHermesSource:
    checkout: Path
    python: Path


class OfficialHermesSourceError(RuntimeError):
    pass


def resolve_official_hermes_source(
    environ: Mapping[str, str],
    default_checkout: Path = DEFAULT_HERMES_SOURCE,
) -> OfficialHermesSource | None:
    configured_checkout = environ.get(HERMES_OFFICIAL_SOURCE_ENV)
    checkout = Path(configured_checkout) if configured_checkout else default_checkout
    if not checkout.is_dir():
        if configured_checkout:
            message = f"{HERMES_OFFICIAL_SOURCE_ENV} does not exist: {checkout}"
            raise OfficialHermesSourceError(message)
        return None

    hermes_python = checkout / ".venv" / "Scripts" / "python.exe"
    if not hermes_python.is_file():
        message = f"official Hermes venv Python does not exist: {hermes_python}"
        raise OfficialHermesSourceError(message)
    return OfficialHermesSource(checkout=checkout, python=hermes_python)


def test_real_hermes_plugin_context_clean_registration_contract() -> None:
    # Given: the official Hermes PluginContext, ToolRegistry, and manager-owned state.
    official_source = resolve_official_hermes_source(os.environ)
    if official_source is None:
        pytest.skip(f"official Hermes source unavailable; set {HERMES_OFFICIAL_SOURCE_ENV}")
    repo_root = Path(__file__).resolve().parents[1]
    script = "\n".join(
        (
            "import json",
            "import sys",
            f"sys.path.insert(0, {str(repo_root)!r})",
            f"sys.path.insert(0, {str(official_source.checkout)!r})",
            "from hermes_cli.plugins import PluginContext, PluginManifest",
            "from office_core_plugin import plugin",
            "from tools.registry import ToolRegistry",
            "import tools.registry as registry_module",
            "class MinimalManager:",
            "    def __init__(self):",
            "        self._cli_ref = None",
            "        self._hooks = {}",
            "        self._plugin_tool_names = set()",
            "        self._plugin_commands = {}",
            "        self._plugin_skills = {}",
            "registry = ToolRegistry()",
            "registry_module.registry = registry",
            "manager = MinimalManager()",
            'ctx = PluginContext(PluginManifest(name="office-core"), manager)',
            "plugin.register(ctx)",
            "print(json.dumps({",
            '    "tools": registry.get_all_tool_names(),',
            '    "tracked": sorted(manager._plugin_tool_names),',
            '    "hooks": sorted(manager._hooks),',
            '    "commands": sorted(manager._plugin_commands),',
            '    "skills": sorted(manager._plugin_skills),',
            "}))",
        ),
    )

    # When: the external plugin registers through the official context shape.
    result = subprocess.run(  # noqa: S603
        [str(official_source.python), "-c", script],
        cwd=official_source.checkout,
        text=True,
        capture_output=True,
        check=False,
    )

    # Then: Hermes-visible state contains exactly this plugin's surface.
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert tuple(proof["tools"]) == EXPECTED_TOOLS
    assert tuple(proof["tracked"]) == EXPECTED_TOOLS
    assert proof["hooks"] == ["post_tool_call"]
    assert proof["commands"] == ["office_status"]
    assert proof["skills"] == sorted(EXPECTED_SKILLS)


def test_official_hermes_source_is_optional_when_implicit_default_missing(tmp_path: Path) -> None:
    # Given: CI has no local official Hermes checkout at the developer default path.
    missing_checkout = tmp_path / "missing-hermes-agent"

    # When/Then: the implicit default is treated as an unavailable optional probe.
    assert resolve_official_hermes_source({}, default_checkout=missing_checkout) is None


def test_official_hermes_source_rejects_explicit_missing_checkout(tmp_path: Path) -> None:
    # Given: a caller explicitly requests an official Hermes checkout path.
    missing_checkout = tmp_path / "missing-hermes-agent"

    # When/Then: a missing explicit path fails instead of silently skipping verification.
    with pytest.raises(OfficialHermesSourceError, match=HERMES_OFFICIAL_SOURCE_ENV):
        resolve_official_hermes_source({HERMES_OFFICIAL_SOURCE_ENV: str(missing_checkout)})

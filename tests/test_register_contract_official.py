from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.register_contract_helpers import EXPECTED_SKILLS, EXPECTED_TOOLS


def test_real_hermes_plugin_context_clean_registration_contract() -> None:
    # Given: the official Hermes PluginContext, ToolRegistry, and manager-owned state.
    hermes_source = Path(r"C:\Users\88697\AppData\Local\hermes\hermes-agent")
    hermes_python = hermes_source / ".venv" / "Scripts" / "python.exe"
    repo_root = Path(__file__).resolve().parents[1]
    script = "\n".join(
        (
            "import json",
            "import sys",
            f"sys.path.insert(0, {str(repo_root)!r})",
            f"sys.path.insert(0, {str(hermes_source)!r})",
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
        [str(hermes_python), "-c", script],
        cwd=hermes_source,
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

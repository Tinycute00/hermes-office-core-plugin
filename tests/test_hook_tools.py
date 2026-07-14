from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "hooks" / "tool_guard_hook.py"
CORE_SCRIPT = ROOT / "skills" / "office-os" / "scripts" / "office_os.py"
MANAGER_SCRIPT = ROOT / "scripts" / "officecli_manager.py"


class ToolGuardCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def run_guard(
        self,
        payload: dict[str, Any],
        *,
        plugin_data: Path | None = None,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if plugin_data is None:
            environment.pop("PLUGIN_DATA", None)
        else:
            environment["PLUGIN_DATA"] = os.fspath(plugin_data)
        completed = subprocess.run(
            [sys.executable, "-B", os.fspath(ENTRYPOINT)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            cwd=self.workspace,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def assert_no_state(self) -> None:
        self.assertFalse(self.plugin_data.exists())

    def pretool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

    def permission(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "hook_event_name": "PermissionRequest",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

    def test_real_entrypoint_noops_generic_spoofed_and_wrong_events_without_state(
        self,
    ) -> None:
        payloads = (
            self.pretool("Bash", {"command": "git status"}),
            self.pretool("mcp__other__tool", {"command": ["set", "anything"]}),
            self.pretool(
                "Bash",
                {"command": 'python "C:\\other\\office_os.py" status'},
            ),
            self.pretool(
                "Bash",
                {"command": f'python "{CORE_SCRIPT}" status | more'},
            ),
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__officecli__officecli",
                "tool_input": {"command": ["validate", "candidate.xlsx"]},
            },
            {"hook_event_name": ["PreToolUse"], "tool_name": "Bash"},
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.run_guard(payload, plugin_data=self.plugin_data), {})
                self.assert_no_state()

    def test_real_entrypoint_adds_neutral_core_context_for_valid_mcp(self) -> None:
        sentinel = "do-not-persist-command-or-prompt"
        result = self.run_guard(
            self.pretool(
                "mcp__officecli__officecli",
                {"command": ["validate", "candidate.xlsx", sentinel]},
            ),
            plugin_data=self.plugin_data,
        )

        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", output)
        self.assertIn("Core", output["additionalContext"])
        self.assertNotIn(sentinel, json.dumps(result))
        self.assert_no_state()

    def test_real_entrypoint_recognizes_only_installed_core_and_manager_bash_paths(
        self,
    ) -> None:
        commands = (
            f'python "{CORE_SCRIPT}" status',
            f'python "{MANAGER_SCRIPT}" status',
        )

        for command in commands:
            with self.subTest(command=command):
                result = self.run_guard(
                    self.pretool("Bash", {"command": command}),
                    plugin_data=self.plugin_data,
                )
                output = result["hookSpecificOutput"]
                self.assertEqual(output["hookEventName"], "PreToolUse")
                self.assertNotIn("permissionDecision", output)
                self.assertNotIn(command, json.dumps(result))
                self.assert_no_state()

    def test_real_entrypoint_denies_malformed_exact_mcp_without_state(self) -> None:
        malformed_inputs = (
            {"command": "not-an-array"},
            {"command": ["validate", 1]},
            {"command": ["validate"] * 129},
            {"command": ["validate", "candidate.xlsx"], "raw": True},
        )

        for tool_input in malformed_inputs:
            with self.subTest(tool_input=tool_input):
                result = self.run_guard(
                    self.pretool("mcp__officecli__officecli", tool_input),
                    plugin_data=self.plugin_data,
                )
                output = result["hookSpecificOutput"]
                self.assertEqual(output["hookEventName"], "PreToolUse")
                self.assertEqual(output["permissionDecision"], "deny")
                self.assertIn("event_protocol", output["permissionDecisionReason"])
                self.assertNotIn("not-an-array", json.dumps(result))
                self.assert_no_state()

    def test_real_entrypoint_denies_explicit_outside_mutation_target_without_state(
        self,
    ) -> None:
        source = self.workspace / "source.xlsx"
        source.write_text("source", encoding="utf-8")
        result = self.run_guard(
            self.pretool(
                "mcp__officecli__officecli",
                {
                    "command": [
                        "set",
                        os.fspath(source),
                        "slide[0]",
                        "--prop",
                        "text=updated",
                    ]
                },
            ),
            plugin_data=self.plugin_data,
        )

        output = result["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("candidate_validation", output["permissionDecisionReason"])
        self.assertNotIn(os.fspath(source), json.dumps(result))
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assert_no_state()

    def test_real_entrypoint_denies_recognized_calls_without_plugin_data(self) -> None:
        result = self.run_guard(
            self.pretool(
                "mcp__officecli__officecli",
                {"command": ["validate", "candidate.xlsx"]},
            )
        )

        output = result["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("launcher_environment", output["permissionDecisionReason"])
        self.assert_no_state()

    def test_permission_request_never_allows_and_unrelated_requests_noop(self) -> None:
        valid = self.run_guard(
            self.permission(
                "mcp__officecli__officecli",
                {"command": ["validate", "candidate.xlsx"]},
            ),
            plugin_data=self.plugin_data,
        )
        output = valid.get("hookSpecificOutput", {})
        self.assertNotEqual(output.get("permissionDecision"), "allow")
        self.assertNotIn("updatedInput", output)
        self.assertNotIn("updatedPermissions", output)
        self.assert_no_state()

        unrelated = self.run_guard(
            self.permission("Bash", {"command": "echo generic"}),
            plugin_data=self.plugin_data,
        )
        self.assertEqual(unrelated, {})
        self.assert_no_state()


if __name__ == "__main__":
    unittest.main()

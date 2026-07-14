from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC_CODES = (
    "config_trust",
    "launcher_environment",
    "event_protocol",
    "state_safety",
    "consent_policy",
    "runtime_integrity",
    "process_timeout",
    "candidate_validation",
    "publish_recovery",
    "final_reply",
)


class HookDocumentationContractCase(unittest.TestCase):
    def test_progressive_hook_and_debugging_references(self) -> None:
        skill = (ROOT / "skills" / "office-os" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        hooks = (ROOT / "skills" / "office-os" / "references" / "Hooks.md").read_text(
            encoding="utf-8"
        )
        debugging = (
            ROOT / "skills" / "office-os" / "references" / "Debugging.md"
        ).read_text(encoding="utf-8")
        agent = (ROOT / "skills" / "office-os" / "references" / "Agent.md").read_text(
            encoding="utf-8"
        )
        officecli = (
            ROOT / "skills" / "office-os" / "references" / "OfficeCLI.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("[Hooks.md](references/Hooks.md)", skill)
        self.assertIn("[Debugging.md](references/Debugging.md)", skill)
        self.assertNotIn("config_trust", skill)
        shipped_events = set(
            json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        )
        documented_events = set(
            re.findall(r"^\| `([^`]+)` \|", hooks, flags=re.MULTILINE)
        )
        self.assertEqual(documented_events, shipped_events)
        self.assertIn("`^(Bash|mcp__officecli__officecli)$`", hooks)
        self.assertIn("advisory", hooks.lower())
        self.assertIn("never return `allow`", hooks)
        self.assertIn("Core and local adapter retain authority", hooks)
        self.assertIn("not hard enforcement", hooks.lower())
        self.assertNotIn("hooks are hard enforcement", hooks.lower())

        for code in DIAGNOSTIC_CODES:
            self.assertEqual(debugging.count(f"`{code}`"), 1, code)
            row = next(
                line for line in debugging.splitlines() if line.startswith(f"| `{code}` |")
            )
            self.assertEqual(len(row.split("|")), 6, code)
            self.assertTrue(row.split("|")[2].strip(), code)
        for marker in (
            "`PLUGIN_DATA/latest_hook_diagnostic.json`", "replace-only",
            "24 hours", "raw prompt", "Office content", "credential",
            "absolute path", "command", "tool response", "PID", "user name",
            "stdout/stderr", "history", "three-hypothesis", "real-QA",
            "Observable safe signal", "cannot prove live host hook dispatch",
            "cannot prove persisted hook trust", "live trusted QA remains required",
        ):
            self.assertIn(marker, debugging)
        self.assertIn("Continue through Codex's installed", officecli)
        self.assertIn("one short question", agent)
        self.assertIn("two automatic continuations", agent)
        self.assertIn("six managed hook groups", readme)
        self.assertIn("six managed hook groups", agents)
        public_docs = "\n".join(
            (skill, hooks, debugging, agent, officecli, readme, agents)
        ).lower()
        for stale in (
            "three lifecycle hooks", "three marker-tagged entries",
            "all three hook events", "those three entries",
        ):
            self.assertNotIn(stale, public_docs)

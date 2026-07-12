from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContractCase(unittest.TestCase):
    def test_plugin_manifest_and_default_hook_location(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "office-os")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertNotIn("hooks", manifest)
        self.assertTrue((ROOT / "hooks" / "hooks.json").is_file())
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"]), 3)

        mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(
            mcp,
            {
                "mcpServers": {
                    "officecli": {
                        "command": "node",
                        "args": ["./scripts/officecli-mcp.cjs"],
                        "cwd": ".",
                    }
                }
            },
        )

    def test_skill_is_single_model_invoked_workflow_with_direct_references(self) -> None:
        skill = ROOT / "skills" / "office-os" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 500)
        self.assertNotIn("TODO", text)
        frontmatter = text.split("---", 2)[1]
        keys = {
            line.split(":", 1)[0].strip()
            for line in frontmatter.splitlines()
            if ":" in line
        }
        self.assertEqual(keys, {"name", "description"})
        references = {
            "Agent.md",
            "Workflow.md",
            "Office.md",
            "Excel.md",
            "Word.md",
            "PowerPoint.md",
            "PDF.md",
            "OfficeCLI.md",
        }
        actual = {
            path.name
            for path in (ROOT / "skills" / "office-os" / "references").glob("*.md")
        }
        self.assertEqual(actual, references)
        self.assertEqual(list((ROOT / "skills").glob("*/SKILL.md")), [skill])
        for reference in sorted(references):
            self.assertIn(f"[{reference}](references/{reference})", text)

    def test_office_workflow_policy_contract(self) -> None:
        text = (ROOT / "skills" / "office-os" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        exactly_once = (
            "意圖：<意圖>｜物件：<物件>｜權限：<權限>｜檢查：<檢查>",
            "ask exactly one short question at a time",
            "PLUGIN_DATA/officecli-candidates",
            "at most 32 files and 2 GiB",
            "replace the same stable target",
            "Manual replacement keeps no history",
            "`.bak.1`, `.bak.2`, and `.bak.3`",
            "latest_summary.json",
            "Only after the result is complete and accepted",
        )
        for marker in exactly_once:
            self.assertEqual(text.count(marker), 1, marker)
        for semantic_unit in (
            "Excel: sheet → table/range → formula family/chart",
            "Word: heading/topic → paragraph/table",
            "PowerPoint: slide → shape tree",
        ):
            self.assertIn(semantic_unit, text)

    def test_workflow_has_no_unbounded_or_automatic_policy(self) -> None:
        text = (ROOT / "skills" / "office-os" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            ".bak.4",
            "candidate or a copy under `Office OS Output`",
            "When the result is accepted or no revision is requested",
            "create a schedule automatically unless",
            "timestamped copies",
            "caller-selected candidate root",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("Never create a schedule automatically", text)
        self.assertIn("Never create timestamped output identities", text)

    def test_officecli_reference_matches_adapter_contract(self) -> None:
        text = (
            ROOT / "skills" / "office-os" / "references" / "OfficeCLI.md"
        ).read_text(encoding="utf-8")
        required = (
            "exactly one tool",
            "command: string[]",
            "PLUGIN_DATA/officecli-candidates",
            "1.0.135",
            "d2d9c60f44537004c3e1f46680c24ea38d9659c2",
            "`validate`, `get`, `query`, `view`, `set`, `add`, `remove`, `move`, and `swap`",
            "`raw`",
            "`batch`",
            "60 seconds",
            "120 seconds",
            "8 MiB",
            "16 MiB",
            "child-command environment only",
            "image/png",
            "removes the temporary image in `finally`",
            "never launches OfficeCLI's upstream MCP mode",
            "files older than 24 hours",
            "at most 32 ordinary files",
            "at most 2 GiB",
        )
        for marker in required:
            self.assertIn(marker, text)
        for stale in (
            "command string",
            "officecli mcp",
            "direct binary fallback",
            "raw package",
            "background MCP",
            "caller-selected candidate root",
        ):
            self.assertNotIn(stale, text)

    def test_office_knowledge_map_matches_runtime_bounds(self) -> None:
        text = (
            ROOT / "skills" / "office-os" / "references" / "Office.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "Latest 256 live stable tasks",
            "PLUGIN_DATA/officecli-candidates",
            "cap staging at 32 files and 2 GiB",
            "`Office OS Output`",
            "Manual publishing keeps no history",
            "`.bak.1`, `.bak.2`, and `.bak.3`",
            "latest_summary only",
            "At most\n32 approved roots",
            "rejects index paths outside",
            "Do not answer from a stale chunk",
            "metadata-only",
            "not an absolute crash-safe transaction",
        ):
            self.assertIn(marker, text)
        for stale in (
            "One row per stable task",
            ".bak.4",
            "automatic installation and updates disabled",
            "cross-platform MCP",
        ):
            self.assertNotIn(stale, text)

    def test_public_docs_match_managed_runtime(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for marker in (
            "local adapter",
            "exactly one",
            "PLUGIN_DATA/officecli-candidates",
            "d2d9c60f44537004c3e1f46680c24ea38d9659c2",
            "No executable is downloaded until the owner approves it",
            "child-command environment only",
            "Codex's installed spreadsheet, document, presentation, or PDF capabilities",
            "Manual work keeps no history",
            "`.bak.1`, `.bak.2`, and `.bak.3`",
        ):
            self.assertIn(marker, readme)
        for marker in (
            "scripts/officecli-mcp/**",
            "tests/test_officecli.py",
            "tests/test_contracts.py",
            "canonical top-level `mcpServers`",
            "child-command environment control",
        ):
            self.assertIn(marker, agents)
        combined = f"{readme}\n{agents}"
        for stale in (
            "transparent upstream",
            "officecli mcp",
            "background update",
            "manual history",
            ".bak.4",
        ):
            self.assertNotIn(stale, combined)

    def test_hooks_cover_only_detection_restore_and_bounded_continuation(self) -> None:
        hooks = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "Stop"})
        self.assertEqual(
            hooks["SessionStart"][0]["matcher"],
            "startup|resume|clear|compact",
        )
        self.assertNotIn("matcher", hooks["UserPromptSubmit"][0])
        self.assertNotIn("matcher", hooks["Stop"][0])
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    self.assertEqual(handler["type"], "command")
                    self.assertIn("commandWindows", handler)
                    self.assertLessEqual(handler["timeout"], 10)

    def test_old_hermes_package_surface_is_removed(self) -> None:
        self.assertFalse((ROOT / "office_core_plugin").exists())
        self.assertFalse((ROOT / "plugin.yaml").exists())
        self.assertFalse((ROOT / "uv.lock").exists())
        self.assertFalse((ROOT / "MANIFEST.in").exists())


if __name__ == "__main__":
    unittest.main()

# noqa: SIZE_OK - Public plugin contracts are intentionally verified together.
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def test_skill_description_starts_with_source_free_intake_contract(self) -> None:
        text = (ROOT / "skills" / "office-os" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        frontmatter = text.split("---", 2)[1]
        description = next(
            line.removeprefix("description: ").strip('"')
            for line in frontmatter.splitlines()
            if line.startswith("description:")
        )

        self.assertTrue(
            description.startswith(
                "Source-free Office intake: first visible line is the exact hook-supplied intent envelope plus office-os rationale"
            ),
            description,
        )
        self.assertIn("SKILL.md is ASCII-only and loaded once", description)
        self.assertIn("final reply repeats the envelope", description)
        self.assertIn("intent envelope, then source-path question", description)
        self.assertNotIn("Prefer", description)
        self.assertIn("No Office data work before source", description)

    def test_skill_source_has_no_bom_for_codex_discovery(self) -> None:
        source = (ROOT / "skills" / "office-os" / "SKILL.md").read_bytes()
        self.assertFalse(source.startswith(b"\xef\xbb\xbf"), source[:3].hex())
        self.assertTrue(source.isascii(), "SKILL.md must be safe under legacy text readers")

    def test_office_workflow_policy_contract(self) -> None:
        text = (ROOT / "skills" / "office-os" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        exactly_once = (
            "Use the exact localized envelope shape supplied by the Office OS hook",
            "ask exactly one short question at a time",
            "PLUGIN_DATA/officecli-candidates",
            "at most 32 files and 2 GiB",
            "replace the same stable target",
            "collision-safe",
            "internal relationship targets",
            "Manual replacement keeps no history",
            "`.bak.1`, `.bak.2`, and `.bak.3`",
            "latest_summary.json",
            "Only after the result is complete and accepted",
        )
        for marker in exactly_once:
            self.assertEqual(text.count(marker), 1, marker)
        for semantic_unit in (
            "Excel: sheet -> table/range -> formula family/chart",
            "Word: heading/topic -> paragraph/table",
            "PowerPoint: slide -> shape tree",
        ):
            self.assertIn(semantic_unit, text)
        self.assertIn("authoritative `PLUGIN_DATA` value injected by the Office OS hook", text)
        self.assertIn("Never run a core or manager command without that exact value", text)
        self.assertNotIn("Create the candidate in the target directory", text)
        self.assertIn("Source-free creation is not a `begin` workflow", text)
        self.assertIn("symlink, junction/reparse point, or hard link", text)

    def test_intake_final_reply_contract_is_shared(self) -> None:
        texts = (
            (ROOT / "skills" / "office-os" / "SKILL.md").read_text(
                encoding="utf-8"
            ),
            (ROOT / "skills" / "office-os" / "references" / "Agent.md").read_text(
                encoding="utf-8"
            ),
        )
        for text in texts:
            for marker in (
                "exactly one final assistant message",
                "first line is the intent envelope",
                "exactly one short question after the envelope",
                "same final message",
                "no visible preamble, plan, skill announcement, tool-activity summary, or separate progress message",
            ):
                self.assertIn(marker, text)
            self.assertIn("with a named local source path or folder", text)
            self.assertIn(
                "exactly one final assistant message with exactly two non-empty lines",
                text,
            )
            self.assertIn(
                "first line is the intent envelope and the second line is the one short source question",
                text,
            )
            self.assertIn("<required-final-reply>", text)
            self.assertIn("<required-first-user-visible-line>", text)
            self.assertIn(
                "first user-visible text line must equal the supplied skill-use announcement verbatim",
                text.lower(),
            )
            self.assertIn(
                "The supplied line already states which skill is being used and why",
                text,
            )
            self.assertIn("copy", text.lower())
            self.assertIn("verbatim", text)
            self.assertIn("Get-Content -Raw -Encoding UTF8", text)
            self.assertIn(
                "SKILL.md is ASCII-only and should be loaded exactly once",
                text,
            )
            self.assertNotIn("Prefer the canonical envelope", text)

    def test_readme_describes_source_free_intake_as_classification_first(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("compact intent classification", text)
        self.assertIn("not supplied a local source", text)
        self.assertIn("before it inspects or alters Office data", text)

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
            "hard-link",
            "across another workspace's cleanup",
            "nested link objects are removed",
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
            "1-100 results and 1-8,000 text characters",
            "required Open XML roots",
            "digest suffix",
            "active revision across all workspaces",
            "Retain at most 256 ordinary workspace-state directories",
            "never an active/running state",
            "do not traverse reparse points",
            "symlink, junction/reparse point, or hard link",
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

    def test_hook_refuses_writable_work_without_plugin_data(self) -> None:
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment.pop("CLAUDE_PLUGIN_DATA", None)

        completed = subprocess.run(
            [sys.executable, os.fspath(ROOT / "hooks" / "office_hook.py")],
            input=json.dumps({"hook_event_name": "SessionStart", "cwd": os.fspath(ROOT)}),
            cwd=ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "Office OS requires the plugin-owned PLUGIN_DATA value.",
            completed.stderr,
        )

    def test_hook_exposes_its_authoritative_plugin_data_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            expected_data_root = data_root.resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            environment.pop("CLAUDE_PLUGIN_DATA", None)

            completed = subprocess.run(
                [sys.executable, os.fspath(ROOT / "hooks" / "office_hook.py")],
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "cwd": os.fspath(workspace),
                    }
                ),
                cwd=workspace,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0)
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertIn(
            f"Authoritative Office OS PLUGIN_DATA is {expected_data_root}.",
            context,
        )

    def test_manual_candidate_mutation_requires_core_confirmation(self) -> None:
        wording = "Manual candidate mutation requires Core confirmation."
        documents = (
            ROOT / "skills" / "office-os" / "SKILL.md",
            ROOT / "skills" / "office-os" / "references" / "Agent.md",
            ROOT / "skills" / "office-os" / "references" / "OfficeCLI.md",
        )
        for document in documents:
            self.assertIn(wording, document.read_text(encoding="utf-8"), document.name)

    def test_old_hermes_package_surface_is_removed(self) -> None:
        self.assertFalse((ROOT / "office_core_plugin").exists())
        self.assertFalse((ROOT / "plugin.yaml").exists())
        self.assertFalse((ROOT / "uv.lock").exists())
        self.assertFalse((ROOT / "MANIFEST.in").exists())


if __name__ == "__main__":
    unittest.main()

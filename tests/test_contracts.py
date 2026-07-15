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
RENDERER = ROOT / "scripts" / "render_office_hooks.py"
HOOK_CONTRACT = {
    "SessionStart": (
        "startup|resume|clear|compact",
        "session_context_hook.py",
        "載入 Office OS",
    ),
    "UserPromptSubmit": (None, "intake_router_hook.py", "辨識辦公室需求"),
    "PreToolUse": (
        "^(Bash|mcp__officecli__officecli)$",
        "tool_guard_hook.py",
        "檢查 Office 工具",
    ),
    "PermissionRequest": (
        "^(Bash|mcp__officecli__officecli)$",
        "tool_guard_hook.py",
        "確認 Office 權限",
    ),
    "PostToolUse": (
        "^(Bash|mcp__officecli__officecli)$",
        "tool_outcome_hook.py",
        "整理 Office 工具結果",
    ),
    "Stop": (None, "completion_hook.py", "確認 Office OS 進度"),
}


class ContractCase(unittest.TestCase):
    def test_plugin_manifest_and_global_hook_registry(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "office-os")
        self.assertRegex(manifest["version"], r"^0\.3\.0(?:\+codex\.[a-z0-9-]+)?$")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertNotIn("hooks", manifest)
        self.assertTrue((ROOT / "hooks" / "hooks.json").is_file())
        self.assertTrue(RENDERER.is_file())
        self.assertTrue((ROOT / "scripts" / "office_hook_registry.py").is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("scripts/install_office_os.ps1", readme)
        self.assertIn("office_hook_registry.py uninstall", readme)
        self.assertIn("~/.codex/hooks.json", readme)
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
            "Hooks.md",
            "Debugging.md",
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
                "Source-free Office intake: first visible message classifies the request and explains the office-os choice"
            ),
            description,
        )
        self.assertIn("SKILL.md is ASCII-only and loaded once", description)
        self.assertIn("Stop validates the exact final envelope once", description)
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
            self.assertIn(
                "first user-visible message must be one compact classification-and-skill-rationale sentence",
                text.lower(),
            )
            self.assertIn(
                "classify the Office workflow, state the read-only boundary, name office-os with or without the `$` invocation sigil, and explain why it applies",
                text,
            )
            self.assertIn("copy", text.lower())
            self.assertIn("verbatim", text)
            self.assertIn("Stop hook", text)
            self.assertIn("Get-Content -Raw -Encoding UTF8", text)
            self.assertIn(
                "SKILL.md is ASCII-only and should be loaded exactly once",
                text,
            )
            self.assertIn("private intake marker", text)
            self.assertIn("one private intake marker per session", text)
            self.assertIn("stores no raw prompt", text)
            for disclosure in (
                "SHA-256",
                "session-plus-turn",
                "derived canonical reply",
                "creation time",
            ):
                self.assertIn(disclosure, text)
            self.assertIn("caps live markers at 128", text)
            self.assertIn("expires them after one hour", text)
            self.assertIn("consumes the matching marker at Stop", text)
            self.assertIn("Every later UserPromptSubmit", text)
            self.assertIn("clears an older marker", text)
            self.assertIn(
                "a newer source-free turn in the same session replaces the older marker",
                text,
            )
            self.assertIn(
                "Stop can consume the same-session marker when the host advances the turn id",
                text,
            )
            self.assertIn("only within that source-free response cycle", text)
            self.assertNotIn("Prefer the canonical envelope", text)
            self.assertNotIn("must equal the supplied skill-use announcement verbatim", text.lower())

    def test_readme_describes_source_free_intake_as_classification_first(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("compact intent classification", text)
        self.assertIn("not supplied a local source", text)
        self.assertIn("before it inspects or alters Office data", text)
        self.assertIn("never stores the raw prompt", text)
        self.assertIn("one private intake marker per session", text)
        for disclosure in (
            "SHA-256",
            "session-plus-turn",
            "derived canonical reply",
            "creation time",
        ):
            self.assertIn(disclosure, text)
        self.assertIn("caps live markers at 128", text)
        self.assertIn("expires them after one hour", text)
        self.assertIn("Every later UserPromptSubmit", text)
        self.assertIn("clears an older marker", text)
        self.assertIn("newer source-free turn in the same session replaces", text)
        self.assertIn("same-session marker when the host advances the turn id", text)

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
            "PLUGIN_DATA/pending_intakes.json",
            "Retain at most 128 live intake markers",
            "expire them after one hour",
            "stores no raw prompt",
            "SHA-256",
            "session-plus-turn",
            "derived canonical reply",
            "creation time",
            "Every later UserPromptSubmit",
            "clears an older marker",
            "newer source-free turn in the same session replaces",
            "same-session marker when the host advances the turn id",
            "only within that source-free response cycle",
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

    def test_generated_hooks_cover_the_six_event_contract(self) -> None:
        hooks = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        self.assertEqual(set(hooks), set(HOOK_CONTRACT))
        for event, (matcher, entrypoint, status_message) in HOOK_CONTRACT.items():
            groups = hooks[event]
            self.assertEqual(len(groups), 1)
            group = groups[0]
            if matcher is None:
                self.assertNotIn("matcher", group)
            else:
                self.assertEqual(group["matcher"], matcher)
            handlers = group["hooks"]
            self.assertEqual(len(handlers), 1)
            handler = handlers[0]
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["timeout"], 10)
            self.assertEqual(handler["statusMessage"], status_message)
            self.assertIn("${PLUGIN_ROOT}", handler["command"])
            self.assertIn("${PLUGIN_DATA}", handler["command"])
            self.assertIn(entrypoint, handler["command"])
            self.assertIn("commandWindows", handler)
            self.assertIn("$env:PLUGIN_ROOT", handler["commandWindows"])
            self.assertIn("$env:PLUGIN_DATA", handler["commandWindows"])
            self.assertIn(entrypoint, handler["commandWindows"])
            self.assertIn("-File", handler["commandWindows"])
            self.assertNotIn("-Command", handler["commandWindows"])
            self.assertNotIn("OFFICE_OS_MANAGED_HOOK=1", handler["command"])
            self.assertNotIn("OFFICE_OS_MANAGED_HOOK=1", handler["commandWindows"])

    def run_renderer(
        self, *arguments: str, expected_returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, os.fspath(RENDERER), *arguments],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected_returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return completed

    def test_hook_renderer_rejects_hand_edited_static_config(self) -> None:
        bundled = ROOT / "hooks" / "hooks.json"
        self.run_renderer("--check")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "hooks.json"
            self.run_renderer("--output", os.fspath(output))
            self.assertEqual(output.read_bytes(), bundled.read_bytes())
            stale = json.loads(output.read_text(encoding="utf-8"))
            stale["hooks"]["PostToolUse"][0]["hooks"][0]["statusMessage"] = "stale"
            output.write_text(
                json.dumps(stale, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            completed = self.run_renderer(
                "--check", "--output", os.fspath(output), expected_returncode=1
            )
        self.assertIn("stale", completed.stderr)

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
            expected_data_root = os.path.normpath(os.fspath(data_root))
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

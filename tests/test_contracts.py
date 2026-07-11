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
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("hooks", manifest)
        self.assertTrue((ROOT / "hooks" / "hooks.json").is_file())
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"]), 3)

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
        self.assertIn("意圖：<意圖>｜物件：<物件>｜權限：<權限>｜檢查：<檢查>", text)
        self.assertIn("ask exactly one short question at a time", text)
        self.assertIn("Office OS Output", text)
        references = {
            "Agent.md",
            "Workflow.md",
            "Office.md",
            "Excel.md",
            "Word.md",
            "PowerPoint.md",
            "PDF.md",
        }
        actual = {
            path.name
            for path in (ROOT / "skills" / "office-os" / "references").glob("*.md")
        }
        self.assertEqual(actual, references)
        skill_files = list((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(skill_files, [skill])

    def test_hooks_cover_only_detection_restore_and_bounded_continuation(self) -> None:
        hooks = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        self.assertEqual(
            set(hooks), {"SessionStart", "UserPromptSubmit", "Stop"}
        )
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

    def test_no_unbounded_timestamp_backup_or_log_contract(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [
                ROOT / "README.md",
                ROOT / "skills" / "office-os" / "SKILL.md",
                ROOT / "skills" / "office-os" / "references" / "Office.md",
                ROOT / "skills" / "office-os" / "references" / "Workflow.md",
            ]
        )
        for suffix in (".bak.1", ".bak.2", ".bak.3"):
            self.assertIn(suffix, combined)
        self.assertNotIn(".bak.4", combined)
        self.assertIn("latest_summary", combined)
        self.assertIn("Last 128", combined)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_office_os.ps1"


class InstallContractCase(unittest.TestCase):
    def test_public_agent_installer_is_documented(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("scripts/install_office_os.ps1", readme)
        self.assertIn("codex plugin add office-os@personal --json", readme)
        self.assertIn("registers plus activates the six managed hook groups", readme)
        self.assertIn("No OfficeCLI executable is downloaded by default", readme)
        self.assertIn("-AcceptOfficeCliDownload", readme)

    def test_installer_registers_personal_plugin_and_hooks(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        for marker in (
            "office-os@personal",
            "scripts\\office_hook_registry.py",
            "--activate",
            "--codex-config",
            "scripts\\officecli_manager.py",
            "--accept-download",
        ):
            self.assertIn(marker, text)

        self.assertIn("Join-Path $HOME 'plugins\\office-os'", text)
        self.assertIn(".codex\\plugin-data\\office-os", text)
        self.assertIn(".codex\\hooks.json", text)
        self.assertIn(".codex\\config.toml", text)

    def test_installer_has_bounded_copy_and_download_consent(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")

        for excluded in ("'.git'", "'.omo'", "'.codegraph'", "'__pycache__'"):
            self.assertIn(excluded, text)
        self.assertIn("existing install root is not an Office OS plugin", text)
        self.assertIn("install root must be the direct personal plugin child", text)
        self.assertIn("if ($AcceptOfficeCliDownload", text)
        self.assertEqual(text.count("install --accept-download"), 1)


if __name__ == "__main__":
    unittest.main()

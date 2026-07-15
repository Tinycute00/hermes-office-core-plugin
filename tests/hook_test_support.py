from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HookCliFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def run_json(
        self, script: Path, payload: dict, *, data_root: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if data_root is None:
            environment.pop("PLUGIN_DATA", None)
        else:
            environment["PLUGIN_DATA"] = os.fspath(data_root)
        return subprocess.run(
            [sys.executable, "-B", os.fspath(script)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            cwd=self.workspace,
            check=False,
        )

    def prompt(self, text: str, turn: str = "turn-1", session: str = "session-1") -> dict[str, str]:
        return {"hook_event_name": "UserPromptSubmit", "session_id": session,
                "turn_id": turn, "cwd": os.fspath(self.workspace), "prompt": text}

    def pending_entries(self) -> list[dict[str, object]]:
        path = self.plugin_data / "pending_intakes.json"
        return json.loads(path.read_text(encoding="utf-8"))["entries"]

    def create_directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        else:
            link.symlink_to(target, target_is_directory=True)
        self.addCleanup(self.remove_directory_link, link)

    def remove_directory_link(self, link: Path) -> None:
        if not os.path.lexists(link):
            return
        if os.name == "nt":
            if not os.path.isjunction(link):
                return
            os.rmdir(link)
        elif link.is_symlink():
            link.unlink()

    def workspace_data(self) -> Path:
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace)))
        identifier = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        directory = self.plugin_data / "workspaces" / identifier
        directory.mkdir(parents=True, exist_ok=True)
        return directory

from __future__ import annotations

import ctypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SESSION_CONTEXT_HOOK = ROOT / "hooks" / "session_context_hook.py"


def load_session_module():
    hooks_root = os.fspath(ROOT / "hooks")
    inserted = hooks_root not in sys.path
    if inserted:
        sys.path.insert(0, hooks_root)
    try:
        return importlib.import_module("office_hooks.session")
    finally:
        if inserted:
            sys.path.remove(hooks_root)


session_module = load_session_module()
state_module = importlib.import_module("office_hooks.state")


def short_windows_path(path: Path) -> Path:
    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetShortPathNameW(
        os.fspath(path), buffer, len(buffer)
    )
    if not length:
        raise RuntimeError("Windows did not provide a short path for fixture.")
    return Path(buffer.value)


class SessionContextHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def workspace_data(self) -> Path:
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace)))
        identifier = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        directory = self.plugin_data / "workspaces" / identifier
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def run_session_hook(
        self, payload: dict[str, str], *, include_plugin_data: bool = True
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment.pop("CLAUDE_PLUGIN_DATA", None)
        if include_plugin_data:
            environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        return subprocess.run(
            [sys.executable, os.fspath(SESSION_CONTEXT_HOOK)],
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=self.workspace,
            env=environment,
            check=False,
        )

    def test_session_start_fresh_root_emits_exact_context_without_state_creation(self) -> None:
        # Given: no plugin data directory exists yet.
        payload = {"hook_event_name": "SessionStart", "cwd": os.fspath(self.workspace)}

        # When: the real SessionStart entrypoint receives the event JSON.
        completed = self.run_session_hook(payload)

        # Then: it emits only the SessionStart context and creates no state root.
        expected = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "Office OS is available as $office-os for local Excel, Word, "
                    "PowerPoint, PDF, and cross-file work. Reclassify the current turn; "
                    "the first visible Office response must begin with an intent classification; "
                    "named-source replies use the Chinese intent envelope."
                    f" Authoritative Office OS PLUGIN_DATA is {self.plugin_data}. "
                    "Set PLUGIN_DATA to exactly this path for every office_os.py command; "
                    "do not use or invent another data root."
                ),
            }
        }
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, json.dumps(expected, separators=(",", ":")) + "\n")
        self.assertEqual(completed.stderr, "")
        self.assertFalse(self.plugin_data.exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_session_context_preserves_configured_long_plugin_data_path(self) -> None:
        directory = self.workspace_data()
        short_directory = short_windows_path(directory)
        captured: list[dict] = []
        with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(self.plugin_data)}):
            with mock.patch.object(session_module, "workspace_dir", return_value=short_directory):
                with mock.patch.object(session_module, "cleanup_stale_temps"):
                    with mock.patch.object(session_module, "read_json", return_value={}):
                        with mock.patch.object(session_module, "emit", side_effect=captured.append):
                            session_module.handle_session_context(
                                {"hook_event_name": "SessionStart", "cwd": os.fspath(self.workspace)}
                            )

        context = captured[0]["hookSpecificOutput"]["additionalContext"]
        self.assertIn(
            f"Authoritative Office OS PLUGIN_DATA is {self.plugin_data}.", context
        )

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_plugin_data_root_never_shortens_the_injected_path(self) -> None:
        self.plugin_data.mkdir()
        short_data = short_windows_path(self.plugin_data)
        if os.path.normcase(os.fspath(short_data)) == os.path.normcase(
            os.fspath(self.plugin_data)
        ):
            self.skipTest("fixture path has no distinct Windows short alias")
        expected = Path(os.path.normpath(os.fspath(self.plugin_data)))
        with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(self.plugin_data)}):
            # Simulate the Windows path expansion that made the old abspath()
            # implementation replace a long injected spelling with an 8.3 alias.
            with mock.patch.object(
                state_module.os.path, "abspath", return_value=os.fspath(short_data)
            ):
                self.assertEqual(state_module.plugin_data_root(), expected)

    def test_session_start_emits_only_compact_active_pointer_and_cleans_stale_temp(self) -> None:
        # Given: one active run plus stale, fresh, and unrelated workspace files.
        directory = self.workspace_data()
        state_path = directory / "run_state.json"
        state = {
            "run_id": "run-session-1",
            "status": "executing",
            "remaining_units": 4,
            "waiting_for_user": False,
            "task": "confidential quarterly report",
            "candidate": "C:/private/candidate.xlsx",
            "continuation_count": 2,
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_before = state_path.read_bytes()
        stale = directory / ".office-os-run_state.json.stale.tmp"
        stale.write_text("stale", encoding="utf-8")
        os.utime(stale, (0, 0))
        fresh = directory / ".office-os-run_state.json.fresh.tmp"
        fresh.write_text("fresh", encoding="utf-8")
        unrelated = directory / "unrelated.txt"
        unrelated.write_text("keep", encoding="utf-8")

        # When: the real category entrypoint receives SessionStart.
        completed = self.run_session_hook(
            {"hook_event_name": "SessionStart", "cwd": os.fspath(self.workspace)}
        )

        # Then: only the compact run pointer is exposed and only stale Office temp state is removed.
        expected_context = (
            "Office OS is available as $office-os for local Excel, Word, "
            "PowerPoint, PDF, and cross-file work. Reclassify the current turn; "
            "the first visible Office response must begin with an intent classification; "
            "named-source replies use the Chinese intent envelope."
            f" Authoritative Office OS PLUGIN_DATA is {self.plugin_data}. "
            "Set PLUGIN_DATA to exactly this path for every office_os.py command; "
            "do not use or invent another data root."
            " Active run: run-session-1; status=executing; remaining_units=4. "
            "Resume only when the current request still belongs to this run."
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": expected_context,
                }
            },
        )
        self.assertNotIn("confidential quarterly report", completed.stdout)
        self.assertNotIn("candidate.xlsx", completed.stdout)
        self.assertNotIn("continuation_count", completed.stdout)
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())
        self.assertFalse((self.plugin_data / "latest_hook_diagnostic.json").exists())

    def test_non_session_start_event_noops_without_plugin_data_access(self) -> None:
        # Given: no authoritative plugin data root is available.
        payload = {"hook_event_name": "Stop", "cwd": os.fspath(self.workspace)}

        # When: the category entrypoint receives an unrelated event.
        completed = self.run_session_hook(payload, include_plugin_data=False)

        # Then: it succeeds with an observable no-op before reading Office state.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "{}\n")
        self.assertEqual(completed.stderr, "")
        self.assertFalse(self.plugin_data.exists())

    def test_session_start_refuses_linked_run_state_without_touching_sentinel(self) -> None:
        # Given: run_state.json is a linked path to an outside sentinel.
        directory = self.workspace_data()
        outside = self.base / "outside-linked-run-state"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")
        state_path = directory / "run_state.json"
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        if os.name == "nt":
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(state_path), os.fspath(outside)],
                capture_output=True,
                encoding="utf-8",
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
        else:
            state_path.symlink_to(outside, target_is_directory=True)
        try:
            # When: SessionStart reads the existing pointer.
            completed = self.run_session_hook(
                {"hook_event_name": "SessionStart", "cwd": os.fspath(self.workspace)}
            )

            # Then: the category fails closed and leaves the outside target untouched.
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stdout, "")
            self.assertIn("unsafe workspace state", completed.stderr)
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
        finally:
            if os.path.lexists(state_path):
                if os.name == "nt":
                    os.rmdir(state_path)
                else:
                    state_path.unlink()

    def test_session_start_refuses_hardlinked_run_state_without_touching_sentinel(self) -> None:
        # Given: run_state.json is a hard link to an outside sentinel.
        directory = self.workspace_data()
        sentinel = self.base / "outside-hardlinked-run-state.json"
        sentinel.write_text('{"status":"executing"}', encoding="utf-8")
        os.link(sentinel, directory / "run_state.json")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()

        # When: SessionStart reads the existing pointer.
        completed = self.run_session_hook(
            {"hook_event_name": "SessionStart", "cwd": os.fspath(self.workspace)}
        )

        # Then: the category fails closed and leaves the outside target untouched.
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn("unsafe workspace state", completed.stderr)
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

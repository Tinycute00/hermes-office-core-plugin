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
COMPLETION_HOOK = ROOT / "hooks" / "completion_hook.py"
LEGACY_HOOK = ROOT / "hooks" / "office_hook.py"
SOURCE_FREE_PROMPT = "請用 $office-os 幫我每週更新 Excel 報表；先不要改檔案。"
CANONICAL_REPLY = "意圖：排程｜物件：Excel｜權限：唯讀｜檢查：快速\nExcel 來源檔或資料夾路徑是什麼？"


class CompletionHookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.plugin_data = self.base / "plugin-data"

    def run_hook(
        self,
        script: Path,
        payload: dict[str, object],
        *,
        include_plugin_data: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = os.fspath(ROOT)
        if include_plugin_data:
            environment["PLUGIN_DATA"] = os.fspath(self.plugin_data)
        else:
            environment.pop("PLUGIN_DATA", None)
        return subprocess.run(
            [sys.executable, os.fspath(script)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            cwd=self.workspace,
            check=False,
        )

    def run_completion(self, payload: dict[str, object]) -> dict[str, object]:
        completed = self.run_hook(COMPLETION_HOOK, payload)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def seed_pending_intake(self, session: str = "session-1", turn: str = "turn-1") -> None:
        completed = self.run_hook(
            LEGACY_HOOK,
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": session,
                "turn_id": turn,
                "cwd": os.fspath(self.workspace),
                "prompt": SOURCE_FREE_PROMPT,
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((self.plugin_data / "pending_intakes.json").is_file())

    def stop_payload(
        self,
        session: str = "session-1",
        turn: str = "turn-1",
        message: str = CANONICAL_REPLY,
        stop_hook_active: bool = False,
    ) -> dict[str, object]:
        return {
            "hook_event_name": "Stop",
            "session_id": session,
            "turn_id": turn,
            "cwd": os.fspath(self.workspace),
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": message,
        }

    def workspace_data(self) -> Path:
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace)))
        identifier = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        directory = self.plugin_data / "workspaces" / identifier
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def create_directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
                text=True,
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
            os.rmdir(link)
        else:
            link.unlink()

    def test_wrong_event_noops_before_plugin_data_access(self) -> None:
        result = self.run_hook(
            COMPLETION_HOOK,
            {"hook_event_name": "SessionStart", "cwd": os.fspath(self.workspace)},
            include_plugin_data=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertFalse(self.plugin_data.exists())

    def test_missing_event_noops_before_plugin_data_access(self) -> None:
        result = self.run_hook(
            COMPLETION_HOOK,
            {"cwd": os.fspath(self.workspace)},
            include_plugin_data=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertFalse(self.plugin_data.exists())

    def test_canonical_final_consumes_matching_pending_intake(self) -> None:
        self.seed_pending_intake()

        result = self.run_completion(self.stop_payload())

        self.assertEqual(result, {})
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

    def test_noncanonical_final_returns_one_correction_and_consumes_marker(self) -> None:
        self.seed_pending_intake()

        result = self.run_completion(self.stop_payload(message="not the canonical reply"))

        self.assertEqual(result["decision"], "block")
        self.assertIn(CANONICAL_REPLY, str(result["reason"]))
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

    def test_moved_turn_consumes_same_session_pending_intake(self) -> None:
        self.seed_pending_intake(turn="turn-original")

        result = self.run_completion(self.stop_payload(turn="turn-moved"))

        self.assertEqual(result, {})
        self.assertFalse((self.plugin_data / "pending_intakes.json").exists())

    def test_cross_session_stop_preserves_other_session_pending_intake(self) -> None:
        self.seed_pending_intake(session="session-owner")

        result = self.run_completion(self.stop_payload(session="session-other"))

        self.assertEqual(result, {})
        self.assertTrue((self.plugin_data / "pending_intakes.json").exists())

    def test_no_progress_does_not_continue_active_run(self) -> None:
        state_path = self.workspace_data() / "run_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "run-no-progress",
                    "status": "executing",
                    "remaining_units": 2,
                    "waiting_for_user": False,
                    "progress_marker": "chunk-1",
                    "last_stop_marker": "chunk-1",
                    "continuation_count": 0,
                }
            ),
            encoding="utf-8",
        )

        result = self.run_completion(self.stop_payload())

        self.assertEqual(result, {})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["continuation_count"], 0)

    def test_progressed_active_run_requests_one_continuation(self) -> None:
        state_path = self.workspace_data() / "run_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "run-capped",
                    "status": "executing",
                    "remaining_units": 3,
                    "waiting_for_user": False,
                    "progress_marker": "chunk-1",
                    "last_stop_marker": "",
                    "continuation_count": 0,
                }
            ),
            encoding="utf-8",
        )

        result = self.run_completion(self.stop_payload())

        self.assertEqual(result["decision"], "block")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["continuation_count"], 1)

    def test_stop_hook_reentry_does_not_continue_or_mutate_progressed_active_run(
        self,
    ) -> None:
        state_path = self.workspace_data() / "run_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "run-stop-reentry",
                    "status": "executing",
                    "remaining_units": 3,
                    "waiting_for_user": False,
                    "progress_marker": "chunk-1",
                    "last_stop_marker": "",
                    "continuation_count": 0,
                }
            ),
            encoding="utf-8",
        )
        before = state_path.read_bytes()

        result = self.run_completion(self.stop_payload(stop_hook_active=True))

        self.assertEqual(result, {})
        self.assertEqual(state_path.read_bytes(), before)

    def test_third_continuation_is_capped(self) -> None:
        state_path = self.workspace_data() / "run_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "run-capped",
                    "status": "executing",
                    "remaining_units": 1,
                    "waiting_for_user": False,
                    "progress_marker": "chunk-3",
                    "last_stop_marker": "chunk-2",
                    "continuation_count": 2,
                }
            ),
            encoding="utf-8",
        )

        result = self.run_completion(self.stop_payload())

        self.assertEqual(result, {})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["continuation_count"], 2)

    def test_linked_pending_intake_fails_closed_without_touching_target(self) -> None:
        self.seed_pending_intake()
        pending = self.plugin_data / "pending_intakes.json"
        pending.unlink()
        outside = self.base / "outside-pending"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        self.create_directory_link(pending, outside)

        result = self.run_hook(COMPLETION_HOOK, self.stop_payload())

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")

    def test_hardlinked_pending_intake_fails_closed_without_touching_target(self) -> None:
        self.seed_pending_intake()
        pending = self.plugin_data / "pending_intakes.json"
        contents = pending.read_bytes()
        pending.unlink()
        sentinel = self.base / "outside-pending.json"
        sentinel.write_bytes(contents)
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        os.link(sentinel, pending)

        result = self.run_hook(COMPLETION_HOOK, self.stop_payload())

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

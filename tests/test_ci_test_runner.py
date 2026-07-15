from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ci_test_runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("ci_test_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def process_exists(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists() and stat.read_text(encoding="utf-8").split()[2:3] == ["Z"]:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def terminate_test_process(pid: int) -> None:
    if not process_exists(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    os.kill(pid, 9)


class CiTestRunnerCase(unittest.TestCase):
    def test_timeout_terminates_the_complete_child_tree(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory(prefix="office-os-ci-runner-") as directory:
            child_pid = Path(directory) / "child.pid"
            child_program = (
                "from pathlib import Path; import os, signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN) if os.name != 'nt' else None; "
                "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
                "time.sleep(60)"
            )
            parent_program = (
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]]); "
                "time.sleep(60)"
            )
            command = [
                sys.executable,
                "-c",
                parent_program,
                os.fspath(child_pid),
                child_program,
            ]

            pid: int | None = None
            try:
                result = runner.run_command(
                    command, timeout_seconds=2.0, grace_seconds=1.0
                )

                self.assertTrue(result.timed_out)
                self.assertEqual(result.exit_code, 124)
                self.assertTrue(child_pid.exists(), "child process did not start")
                pid = int(child_pid.read_text(encoding="utf-8"))
                for _ in range(20):
                    if not process_exists(pid):
                        break
                    time.sleep(0.1)
                self.assertFalse(process_exists(pid), f"orphaned child PID {pid}")
            finally:
                if pid is None and child_pid.exists():
                    pid = int(child_pid.read_text(encoding="utf-8"))
                if pid is not None:
                    terminate_test_process(pid)

    @unittest.skipIf(os.name == "nt", "requires POSIX process groups")
    def test_posix_child_receives_the_group_graceful_shutdown(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory(prefix="office-os-ci-runner-") as directory:
            child_pid = Path(directory) / "child.pid"
            marker = Path(directory) / "graceful.txt"
            child_program = (
                "from pathlib import Path; import os, signal, sys, time; "
                "signal.signal(signal.SIGTERM, lambda *_: "
                "(Path(sys.argv[2]).write_text('graceful', encoding='utf-8'), sys.exit(0))); "
                "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
                "time.sleep(60)"
            )
            parent_program = (
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[3], sys.argv[1], sys.argv[2]]); "
                "time.sleep(60)"
            )
            pid: int | None = None
            try:
                result = runner.run_command(
                    [
                        sys.executable,
                        "-c",
                        parent_program,
                        os.fspath(child_pid),
                        os.fspath(marker),
                        child_program,
                    ],
                    timeout_seconds=2.0,
                    grace_seconds=1.0,
                )

                self.assertTrue(result.timed_out)
                self.assertEqual(result.exit_code, 124)
                self.assertTrue(marker.exists(), "child did not receive SIGTERM")
                pid = int(child_pid.read_text(encoding="utf-8"))
                self.assertFalse(process_exists(pid), f"orphaned child PID {pid}")
            finally:
                if pid is None and child_pid.exists():
                    pid = int(child_pid.read_text(encoding="utf-8"))
                if pid is not None:
                    terminate_test_process(pid)

    @unittest.skipIf(os.name == "nt", "requires POSIX process groups")
    def test_posix_detached_child_is_terminated_after_timeout(self) -> None:
        runner = load_runner()
        with tempfile.TemporaryDirectory(prefix="office-os-ci-runner-") as directory:
            child_pid = Path(directory) / "detached-child.pid"
            child_program = (
                "from pathlib import Path; import os, sys, time; "
                "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
                "time.sleep(60)"
            )
            parent_program = (
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]], "
                "start_new_session=True); "
                "time.sleep(60)"
            )

            pid: int | None = None
            try:
                result = runner.run_command(
                    [
                        sys.executable,
                        "-c",
                        parent_program,
                        os.fspath(child_pid),
                        child_program,
                    ],
                    timeout_seconds=2.0,
                    grace_seconds=1.0,
                )

                self.assertTrue(result.timed_out)
                self.assertEqual(result.exit_code, 124)
                self.assertTrue(child_pid.exists(), "detached child process did not start")
                pid = int(child_pid.read_text(encoding="utf-8"))
                for _ in range(20):
                    if not process_exists(pid):
                        break
                    time.sleep(0.1)
                self.assertFalse(
                    process_exists(pid), f"orphaned detached child PID {pid}"
                )
            finally:
                if pid is None and child_pid.exists():
                    pid = int(child_pid.read_text(encoding="utf-8"))
                if pid is not None:
                    terminate_test_process(pid)

    def test_normal_exit_is_returned_without_timeout(self) -> None:
        runner = load_runner()
        result = runner.run_command(
            [sys.executable, "-c", "raise SystemExit(7)"],
            timeout_seconds=1.0,
            grace_seconds=1.0,
        )

        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 7)


if __name__ == "__main__":
    unittest.main()

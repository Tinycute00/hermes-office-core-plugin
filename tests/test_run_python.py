from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PREFIX = ".test-run-python-"
RUNNER = Path(
    os.environ.get("OFFICE_RUNNER_PATH", os.fspath(ROOT / "hooks" / "run-python.ps1"))
)
FIXTURE = (
    "import os\n"
    "import sys\n"
    "\n"
    "payload = sys.stdin.buffer.read()\n"
    "mode = os.environ[\"RUNNER_FIXTURE_MODE\"]\n"
    "if mode == \"raw\":\n"
    "    sys.stdout.buffer.write(payload)\n"
    "    sys.stdout.buffer.write(b\"\\x00\\xff\\x80\\xfeOUT\\n\")\n"
    "    sys.stderr.buffer.write(b\"\\x00\\xff\\x80\\xfe\\n\")\n"
    "    sys.stdout.buffer.flush()\n"
    "    sys.stderr.buffer.flush()\n"
    "    raise SystemExit(37)\n"
    "if mode == \"large\":\n"
    "    sys.stdout.buffer.write(b\"O\" * (256 * 1024) + b\"\\x00\\xffOUT\\n\")\n"
    "    sys.stderr.buffer.write(b\"E\" * (256 * 1024) + b\"\\x00\\xffERR\\n\")\n"
    "    sys.stdout.buffer.flush()\n"
    "    sys.stderr.buffer.flush()\n"
    "    raise SystemExit(29)\n"
    "raise SystemExit(2)\n"
)


@unittest.skipUnless(
    os.name == "nt" and shutil.which("powershell.exe"),
    "requires Windows PowerShell",
)
class RunPythonPowerShellCase(unittest.TestCase):
    def run_runner(
        self,
        payload: bytes,
        *,
        mode: str,
        runner_path: Path = RUNNER,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[bytes]:
        fixture_root = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX, dir=ROOT))
        process: subprocess.Popen[bytes] | None = None
        try:
            workspace = fixture_root / "workspace"
            workspace.mkdir()
            script = fixture_root / "child fixture.py"
            script.write_text(FIXTURE, encoding="utf-8")
            environment = os.environ.copy()
            environment["RUNNER_FIXTURE_MODE"] = mode
            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                os.fspath(runner_path),
                os.fspath(script),
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                cwd=workspace,
            )
            try:
                stdout, stderr = process.communicate(payload, timeout=timeout)
            except subprocess.TimeoutExpired:
                taskkill = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                self.fail(
                    f"runner deadlocked after {timeout} seconds; "
                    f"taskkill stdout={taskkill.stdout!r} "
                    f"stderr={taskkill.stderr!r}"
                )
            return subprocess.CompletedProcess(
                command, process.returncode, stdout, stderr
            )
        finally:
            if process is not None and process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            shutil.rmtree(fixture_root)

    def test_runner_forwards_exact_utf8_stdin_and_binary_output(self) -> None:
        payload = '{"message":"raw stdin 😀 測試"}\n'.encode("utf-8")
        completed = self.run_runner(payload, mode="raw")

        self.assertEqual(
            completed.returncode,
            37,
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )
        self.assertEqual(
            completed.stdout, payload + b"\x00\xff\x80\xfeOUT\n"
        )
        self.assertEqual(completed.stderr, b"\x00\xff\x80\xfe\n")

    def test_runner_drains_large_stdout_and_stderr_concurrently(self) -> None:
        completed = self.run_runner(b"large-input", mode="large")

        self.assertEqual(completed.returncode, 29, completed.stderr)
        self.assertEqual(completed.stdout, b"O" * (256 * 1024) + b"\x00\xffOUT\n")
        self.assertEqual(completed.stderr, b"E" * (256 * 1024) + b"\x00\xffERR\n")


if __name__ == "__main__":
    unittest.main()

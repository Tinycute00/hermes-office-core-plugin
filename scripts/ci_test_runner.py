"""Run the CI test suite with a bounded, isolated process tree."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import signal
import subprocess
import sys
import time


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    timed_out: bool


def wait_for_process_group_exit(process_group: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def terminate_process_tree(process: subprocess.Popen[object], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            timeout=max(1.0, grace_seconds),
            capture_output=True,
            text=True,
        )
        if process.poll() is None:
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "taskkill did not terminate the timed-out test tree "
                    f"(exit={result.returncode})."
                ) from error
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if wait_for_process_group_exit(process.pid, grace_seconds):
        if process.poll() is None:
            process.wait(timeout=grace_seconds)
        return
    os.killpg(process.pid, signal.SIGKILL)
    if not wait_for_process_group_exit(process.pid, grace_seconds):
        raise RuntimeError("SIGKILL did not terminate the timed-out test tree.")
    if process.poll() is None:
        process.wait(timeout=grace_seconds)


def run_command(
    command: list[str], *, timeout_seconds: float, grace_seconds: float
) -> RunResult:
    if not command:
        raise ValueError("CI test command is required")
    if timeout_seconds <= 0 or grace_seconds <= 0:
        raise ValueError("CI timeouts must be positive")
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, **options)
    try:
        return RunResult(process.wait(timeout=timeout_seconds), timed_out=False)
    except subprocess.TimeoutExpired:
        print(
            f"[CI-WATCHDOG] deadline {timeout_seconds:g}s exceeded; "
            "terminating the isolated test process tree.",
            flush=True,
        )
        try:
            terminate_process_tree(process, grace_seconds)
        except RuntimeError as error:
            print(f"[CI-WATCHDOG] termination failure: {error}", flush=True)
            return RunResult(125, timed_out=True)
        return RunResult(124, timed_out=True)


def parse_arguments(argv: list[str]) -> tuple[float, float, list[str]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=720.0)
    parser.add_argument("--grace-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = list(arguments.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    return arguments.timeout_seconds, arguments.grace_seconds, command


def main(argv: list[str] | None = None) -> int:
    timeout_seconds, grace_seconds, command = parse_arguments(
        sys.argv[1:] if argv is None else argv
    )
    print(
        f"[CI-WATCHDOG] running isolated test tree with {timeout_seconds:g}s deadline.",
        flush=True,
    )
    result = run_command(
        command, timeout_seconds=timeout_seconds, grace_seconds=grace_seconds
    )
    if result.timed_out:
        print("[CI-WATCHDOG] test process tree terminated after deadline.", flush=True)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

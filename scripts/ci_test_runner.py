"""Run the CI test suite with a bounded, isolated process tree."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    timed_out: bool


@dataclass(frozen=True)
class LinuxProcessIdentity:
    pid: int
    parent_pid: int
    process_group: int
    start_ticks: int
    state: str


def read_linux_process_identity(pid: int) -> LinuxProcessIdentity | None:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    _, separator, fields_text = stat.rpartition(")")
    if not separator:
        return None
    fields = fields_text.split()
    if len(fields) <= 19:
        return None
    try:
        return LinuxProcessIdentity(
            pid=pid,
            parent_pid=int(fields[1]),
            process_group=int(fields[2]),
            start_ticks=int(fields[19]),
            state=fields[0],
        )
    except ValueError:
        return None


def snapshot_linux_process_tree(root_pid: int) -> dict[int, LinuxProcessIdentity]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise RuntimeError("Linux CI watchdog requires a readable /proc process table.")
    all_processes: dict[int, LinuxProcessIdentity] = {}
    children: dict[int, list[int]] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        identity = read_linux_process_identity(int(entry.name))
        if identity is None:
            continue
        all_processes[identity.pid] = identity
        children.setdefault(identity.parent_pid, []).append(identity.pid)

    pending = [root_pid]
    tree: dict[int, LinuxProcessIdentity] = {}
    while pending:
        pid = pending.pop()
        identity = all_processes.get(pid)
        if identity is None or pid in tree:
            continue
        tree[pid] = identity
        pending.extend(children.get(pid, []))
    return tree


def linux_process_is_live(identity: LinuxProcessIdentity) -> bool:
    current = read_linux_process_identity(identity.pid)
    return (
        current is not None
        and current.start_ticks == identity.start_ticks
        and current.state not in {"X", "Z"}
    )


def wait_for_linux_processes_exit(
    identities: dict[int, LinuxProcessIdentity], timeout_seconds: float
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if not any(linux_process_is_live(identity) for identity in identities.values()):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def wait_for_posix_process_group_exit(
    process: subprocess.Popen[object], timeout_seconds: float
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def signal_linux_process_tree(
    identities: dict[int, LinuxProcessIdentity], signal_number: int
) -> None:
    own_group = os.getpgrp()
    signalled_groups: set[int] = set()
    for process_group in sorted({item.process_group for item in identities.values()}):
        leader = identities.get(process_group)
        if (
            process_group == own_group
            or leader is None
            or not linux_process_is_live(leader)
        ):
            continue
        try:
            os.killpg(process_group, signal_number)
        except ProcessLookupError:
            continue
        signalled_groups.add(process_group)

    for identity in identities.values():
        if identity.process_group in signalled_groups or not linux_process_is_live(identity):
            continue
        try:
            os.kill(identity.pid, signal_number)
        except ProcessLookupError:
            continue


def wait_for_process_exit(process: subprocess.Popen[object], timeout_seconds: float) -> bool:
    if process.poll() is not None:
        return True
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def terminate_linux_process_tree(
    process: subprocess.Popen[object], grace_seconds: float
) -> None:
    identities = snapshot_linux_process_tree(process.pid)
    if not identities:
        if process.poll() is not None:
            return
        raise RuntimeError("could not snapshot the timed-out Linux test process tree.")
    signal_linux_process_tree(identities, signal.SIGTERM)
    if wait_for_linux_processes_exit(identities, grace_seconds):
        if not wait_for_process_exit(process, grace_seconds):
            raise RuntimeError("SIGTERM did not reap the timed-out test process.")
        return
    signal_linux_process_tree(identities, signal.SIGKILL)
    if not wait_for_linux_processes_exit(identities, grace_seconds):
        raise RuntimeError("SIGKILL did not terminate the timed-out test tree.")
    if not wait_for_process_exit(process, grace_seconds):
        raise RuntimeError("SIGKILL did not reap the timed-out test process.")


def terminate_other_posix_process_tree(
    process: subprocess.Popen[object], grace_seconds: float
) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if wait_for_posix_process_group_exit(process, grace_seconds):
        if not wait_for_process_exit(process, grace_seconds):
            raise RuntimeError("SIGTERM did not reap the timed-out test process.")
        return
    os.killpg(process.pid, signal.SIGKILL)
    if not wait_for_posix_process_group_exit(process, grace_seconds):
        raise RuntimeError("SIGKILL did not terminate the timed-out test tree.")
    if not wait_for_process_exit(process, grace_seconds):
        raise RuntimeError("SIGKILL did not reap the timed-out test process.")


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
    if sys.platform.startswith("linux"):
        terminate_linux_process_tree(process, grace_seconds)
        return
    terminate_other_posix_process_tree(process, grace_seconds)


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

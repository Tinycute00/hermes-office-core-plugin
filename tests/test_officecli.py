from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "officecli_manager.py"
LOCK = ROOT / "vendor" / "officecli.lock.json"
LAUNCHER = ROOT / "scripts" / "officecli-mcp.cjs"


def load_manager():
    scripts = os.fspath(MANAGER.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("officecli_manager", MANAGER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load OfficeCLI manager module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfficeCLICase(unittest.TestCase):
    def test_lock_pins_correct_release(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["project"], "iOfficeAI/OfficeCLI")
        self.assertEqual(lock["version"], "1.0.135")
        self.assertEqual(
            lock["sourceCommit"],
            "d2d9c60f44537004c3e1f46680c24ea38d9659c2",
        )
        self.assertIn(lock["sourceCommit"], lock["license"]["licenseUrl"])
        self.assertIn(lock["sourceCommit"], lock["license"]["noticeUrl"])
        self.assertEqual(lock["license"]["spdx"], "Apache-2.0")
        self.assertEqual(lock["mcpProtocolVersion"], "2024-11-05")
        self.assertEqual(
            set(lock["assets"]),
            {
                "windows-x64",
                "windows-arm64",
                "macos-x64",
                "macos-arm64",
                "linux-x64",
                "linux-arm64",
                "linux-alpine-x64",
                "linux-alpine-arm64",
            },
        )
        for asset in lock["assets"].values():
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("/releases/download/v1.0.135/", asset["url"])
            self.assertTrue(asset["url"].endswith(asset["filename"]))

    def test_child_environment_contains_only_managed_officecli_keys(self) -> None:
        manager = load_manager()
        with mock.patch.dict(
            os.environ,
            {
                "OFFICECLI_SKIP_UPDATE": "0",
                "OFFICECLI_BATCH_ALLOW_STDIN_REDIRECT": "1",
                "OFFICECLI_UNTRUSTED": "present",
                "UNRELATED": "preserved",
            },
            clear=False,
        ):
            environment = manager.side_effect_free_environment()
        officecli = {
            key: value
            for key, value in environment.items()
            if key.startswith("OFFICECLI_")
        }
        self.assertEqual(
            officecli,
            {
                "OFFICECLI_SKIP_UPDATE": "1",
                "OFFICECLI_NO_AUTO_INSTALL": "1",
                "OFFICECLI_NO_AUTO_RESIDENT": "1",
            },
        )
        self.assertEqual(environment["UNRELATED"], "preserved")

    def test_manager_prunes_only_safe_old_siblings(self) -> None:
        manager = load_manager()
        binary = b"verified-officecli"
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            lock = manager.load_lock()
            asset = lock["assets"][manager.current_asset_key()]
            asset["sha256"] = hashlib.sha256(binary).hexdigest()
            current = manager.managed_binary_path(lock, asset, data_root)
            current.parent.mkdir(parents=True)
            current.write_bytes(binary)
            old = data_root / "runtimes" / "officecli" / "0.9.0"
            old.mkdir(parents=True)
            (old / "stale.bin").write_bytes(b"stale")
            sentinel = data_root / "outside-sentinel.bin"
            sentinel.write_bytes(b"outside")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            runtime = sys.modules["officecli_runtime"]
            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                with mock.patch.object(runtime, "load_lock", return_value=lock):
                    first = manager.install_runtime(True)
                    second = manager.install_runtime(True)
            self.assertEqual(first["status"], "already_installed")
            self.assertEqual(second["status"], "already_installed")
            self.assertTrue(current.is_file())
            self.assertFalse(old.exists())
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_manager_refuses_linked_runtime_paths(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            runtime_root = data_root / "runtimes" / "officecli"
            current = runtime_root / "1.0.135"
            current.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.bin"
            sentinel.write_bytes(b"outside")
            linked = runtime_root / "0.9.0"
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            else:
                linked.symlink_to(outside, target_is_directory=True)
            try:
                with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                    with self.assertRaises(manager.OfficeCLIManagerError):
                        manager.prune_old_versions("1.0.135")
                self.assertEqual(sentinel.read_bytes(), b"outside")
                self.assertTrue(current.is_dir())
            finally:
                if os.name == "nt" and linked.exists():
                    os.rmdir(linked)
                elif linked.is_symlink():
                    linked.unlink()

    def test_uninstall_removes_only_managed_versions(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            runtime_root = data_root / "runtimes" / "officecli"
            for version in ("0.9.0", "1.0.135"):
                directory = runtime_root / version
                directory.mkdir(parents=True)
                (directory / "officecli.bin").write_bytes(version.encode())
            sentinel = data_root / "outside-sentinel.bin"
            sentinel.write_bytes(b"outside")
            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                result = manager.uninstall_runtime()
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse(runtime_root.exists())
            self.assertFalse((data_root / "runtimes").exists())
            self.assertEqual(sentinel.read_bytes(), b"outside")

    def test_manager_status_is_read_only_and_reports_missing_runtime(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            completed = subprocess.run(
                [sys.executable, os.fspath(MANAGER), "status"],
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            status = json.loads(completed.stdout)
            self.assertFalse(status["installed"])
            self.assertEqual(status["version"], "1.0.135")
            self.assertFalse(data_root.exists())

    def test_manager_detects_a_tampered_managed_binary(self) -> None:
        manager = load_manager()
        lock = manager.load_lock()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary)
            asset_key = manager.current_asset_key()
            asset = lock["assets"][asset_key]
            target = manager.managed_binary_path(lock, asset, data_root)
            target.parent.mkdir(parents=True)
            target.write_bytes(b"not-officecli")
            status = manager.runtime_status(lock, data_root)
            self.assertFalse(status["installed"])
            self.assertEqual(status["integrity"], "checksum_mismatch")
            self.assertEqual(
                status["actual_sha256"], hashlib.sha256(b"not-officecli").hexdigest()
            )

    @unittest.skipUnless(shutil.which("node"), "Node.js is not available")
    def test_mcp_launcher_fails_closed_when_runtime_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            completed = subprocess.run(
                ["node", os.fspath(LAUNCHER)],
                cwd=ROOT,
                env=environment,
                input="",
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("officecli_manager.py", completed.stderr)
            self.assertIn("install --accept-download", completed.stderr)
            self.assertFalse(data_root.exists())


if __name__ == "__main__":
    unittest.main()

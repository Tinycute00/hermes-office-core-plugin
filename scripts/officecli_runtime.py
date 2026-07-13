from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT: Final = Path(__file__).resolve().parents[1]
LOCK_PATH: Final = ROOT / "vendor" / "officecli.lock.json"
PROJECT: Final = "iOfficeAI/OfficeCLI"
VERSION: Final = "1.0.135"
SOURCE_COMMIT: Final = "d2d9c60f44537004c3e1f46680c24ea38d9659c2"
MAX_DOWNLOAD_BYTES: Final = 512 * 1024 * 1024
MANAGED_ENV: Final = {
    "OFFICECLI_SKIP_UPDATE": "1",
    "OFFICECLI_NO_AUTO_INSTALL": "1",
    "OFFICECLI_NO_AUTO_RESIDENT": "1",
}


class OfficeCLIManagerError(RuntimeError):
    pass


def load_lock() -> dict:  # noqa: DICT_OK
    try:
        value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfficeCLIManagerError("OfficeCLI lock file is missing or invalid.") from error
    if (
        not isinstance(value, dict)
        or value.get("project") != PROJECT
        or value.get("version") != VERSION
        or value.get("sourceCommit") != SOURCE_COMMIT
        or not isinstance(value.get("assets"), dict)
    ):
        raise OfficeCLIManagerError("OfficeCLI lock identity or schema is invalid.")
    return value


def plugin_data_root() -> Path:
    configured = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if not configured:
        raise OfficeCLIManagerError("OfficeCLI requires the hook-injected PLUGIN_DATA value.")
    selected = Path(configured).expanduser()
    return Path(os.path.abspath(os.fspath(selected)))


def normalized_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower().replace("_", "-")
    if value in {"amd64", "x86-64", "x64"}:
        return "x64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    raise OfficeCLIManagerError(f"Unsupported OfficeCLI CPU architecture: {value}")


def linux_uses_musl() -> bool:
    return platform.libc_ver()[0].lower() == "musl" or Path("/etc/alpine-release").exists()


def current_asset_key(
    system: str | None = None,
    machine: str | None = None,
    musl: bool | None = None,
) -> str:
    operating_system = (system or platform.system()).lower()
    arch = normalized_arch(machine)
    if operating_system == "windows":
        return f"windows-{arch}"
    if operating_system == "darwin":
        return f"macos-{arch}"
    if operating_system == "linux":
        alpine = linux_uses_musl() if musl is None else musl
        return f"{'linux-alpine' if alpine else 'linux'}-{arch}"
    raise OfficeCLIManagerError(f"Unsupported OfficeCLI operating system: {operating_system}")


def selected_asset(lock: dict) -> tuple[str, dict]:
    key = current_asset_key()
    asset = lock["assets"].get(key)
    if not isinstance(asset, dict):
        raise OfficeCLIManagerError(f"No locked OfficeCLI asset for {key}.")
    if not all(isinstance(asset.get(field), str) for field in ("filename", "url", "sha256")):
        raise OfficeCLIManagerError(f"Locked OfficeCLI asset for {key} is invalid.")
    return key, asset


def managed_runtime_root(data_root: Path | None = None) -> Path:
    selected = data_root or plugin_data_root()
    return Path(os.path.abspath(os.fspath(selected))) / "runtimes" / "officecli"


def managed_binary_path(lock: dict, asset: dict, data_root: Path | None = None) -> Path:
    return managed_runtime_root(data_root) / str(lock["version"]) / str(asset["filename"])


def is_linklike(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def validate_no_linked_ancestors(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    cursor = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        cursor /= component
        if not os.path.lexists(cursor):
            break
        if is_linklike(cursor):
            raise OfficeCLIManagerError("Managed OfficeCLI runtime path is linked or invalid.")


def validate_runtime_ancestors(
    data_root: Path | None = None,
    version: str | None = None,
) -> Path:
    root = managed_runtime_root(data_root)
    data = root.parents[1]
    validate_no_linked_ancestors(data)
    components = [data, data / "runtimes", root]
    if version is not None:
        components.append(root / version)
    real_data: Path | None = None
    for component in components:
        if not os.path.lexists(component):
            continue
        if is_linklike(component) or not component.is_dir():
            raise OfficeCLIManagerError("Managed OfficeCLI runtime path is linked or invalid.")
        if real_data is None:
            real_data = data.resolve(strict=True)
        try:
            component.resolve(strict=True).relative_to(real_data)
        except ValueError:
            raise OfficeCLIManagerError("Managed OfficeCLI runtime path escapes plugin data.") from None
    return root


def validate_version_siblings(current_version: str, data_root: Path | None = None) -> list[Path]:
    root = validate_runtime_ancestors(data_root)
    if not root.exists():
        return []
    old: list[Path] = []
    for child in root.iterdir():
        if is_linklike(child) or not child.is_dir():
            raise OfficeCLIManagerError("Managed OfficeCLI version entries must be ordinary directories.")
        if child.name != current_version:
            old.append(child)
    return old


def assert_ordinary_binary(binary: Path) -> None:
    validate_runtime_ancestors(binary.parents[3], binary.parent.name)
    if is_linklike(binary):
        raise OfficeCLIManagerError("Managed OfficeCLI binary path must not contain links.")
    try:
        mode = binary.lstat().st_mode
    except FileNotFoundError:
        raise OfficeCLIManagerError("Managed OfficeCLI binary is missing.") from None
    if not stat.S_ISREG(mode):
        raise OfficeCLIManagerError("Managed OfficeCLI binary must be an ordinary file.")
    if binary.lstat().st_nlink > 1:
        raise OfficeCLIManagerError("Managed OfficeCLI binary must not be hard linked.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_status(lock: dict | None = None, data_root: Path | None = None) -> dict:  # noqa: DICT_OK
    value = lock or load_lock()
    key, asset = selected_asset(value)
    target = managed_binary_path(value, asset, data_root)
    validate_runtime_ancestors(data_root, str(value["version"]))
    base = {
        "version": value["version"],
        "asset": key,
        "path": os.fspath(target),
        "expected_sha256": asset["sha256"],
    }
    if not os.path.lexists(target):
        return {**base, "installed": False, "integrity": "missing"}
    assert_ordinary_binary(target)
    actual = sha256_file(target)
    if actual != asset["sha256"]:
        return {**base, "installed": False, "integrity": "checksum_mismatch", "actual_sha256": actual}
    return {**base, "installed": True, "integrity": "verified", "actual_sha256": actual}


def side_effect_free_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OFFICECLI_")}
    environment.update(MANAGED_ENV)
    return environment


def verify_version(binary: Path, expected_version: str) -> str:
    try:
        completed = subprocess.run(
            [os.fspath(binary), "--version"], env=side_effect_free_environment(), text=True,
            encoding="utf-8", errors="replace", capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OfficeCLIManagerError("Downloaded OfficeCLI binary could not be started.") from error
    output = "\n".join(item for item in (completed.stdout, completed.stderr) if item).strip()
    if completed.returncode != 0 or expected_version not in output:
        raise OfficeCLIManagerError(f"Downloaded OfficeCLI version check failed; expected {expected_version}.")
    return output


def download_asset(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "Office-OS-Managed-Runtime/1"})
    try:
        with urlopen(request, timeout=60) as response, destination.open("wb") as handle:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise OfficeCLIManagerError("OfficeCLI download exceeds the size limit.")
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_DOWNLOAD_BYTES:
                    raise OfficeCLIManagerError("OfficeCLI download exceeds the size limit.")
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise OfficeCLIManagerError(f"OfficeCLI download failed: {error}") from error


def prune_old_versions(current_version: str, data_root: Path | None = None) -> int:
    old = validate_version_siblings(current_version, data_root)
    for directory in old:
        shutil.rmtree(directory)
    return len(old)


def install_runtime(accept_download: bool) -> dict:  # noqa: DICT_OK
    if not accept_download:
        raise OfficeCLIManagerError("Installation requires --accept-download after the owner approves the executable download.")
    lock = load_lock()
    validate_version_siblings(str(lock["version"]))
    status = runtime_status(lock)
    if status["installed"]:
        prune_old_versions(str(lock["version"]))
        return {**status, "status": "already_installed", "downloaded": False}
    _, asset = selected_asset(lock)
    target = Path(status["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_runtime_ancestors(version=str(lock["version"]))
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.download{target.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        download_asset(str(asset["url"]), temporary)
        if sha256_file(temporary) != asset["sha256"]:
            raise OfficeCLIManagerError("OfficeCLI download checksum does not match the pinned release.")
        temporary.chmod(temporary.stat().st_mode | 0o700)
        version_output = verify_version(temporary, str(lock["version"]))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    installed = runtime_status(lock)
    if installed.get("installed") is not True or installed.get("integrity") != "verified":
        raise OfficeCLIManagerError("Installed OfficeCLI runtime did not pass post-install verification.")
    prune_old_versions(str(lock["version"]))
    return {**installed, "status": "installed", "downloaded": True, "version_output": version_output, "license": lock["license"]}


def uninstall_runtime() -> dict:  # noqa: DICT_OK
    root = managed_runtime_root()
    versions = validate_version_siblings("__none__")
    removed = bool(versions)
    for directory in versions:
        shutil.rmtree(directory)
    if root.exists() and not any(root.iterdir()):
        root.rmdir()
    runtimes = root.parent
    if runtimes.exists() and not any(runtimes.iterdir()):
        runtimes.rmdir()
    return {"status": "uninstalled" if removed else "not_installed", "removed": removed, "path": os.fspath(root)}

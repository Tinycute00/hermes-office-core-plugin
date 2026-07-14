from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from office_hook_toml import (
    TomlError,
    atomic_write,
    edit_codex_toml,
    read_toml,
    require_directory,
    require_regular_file,
)


MANAGED_MARKER = "OFFICE_OS_MANAGED_HOOK=1"
HOOKS_DIRECTORY = Path(__file__).resolve().parents[1] / "hooks"
if os.fspath(HOOKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, os.fspath(HOOKS_DIRECTORY))

from office_hook_spec import HOOK_DEFINITIONS


ACTIVATION_STATE_NAME = ".office-os-hook-activation.json"


class ActivationError(TomlError):
    pass


def event_label(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def is_managed_handler(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(
        isinstance(value.get(key), str) and MANAGED_MARKER in value[key]
        for key in ("command", "commandWindows")
    )


def is_managed_group(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("hooks"), list)
        and any(is_managed_handler(handler) for handler in value["hooks"])
    )


def _trusted_hash(event: str, group: dict[str, Any], handler: dict[str, Any]) -> str:
    command = handler.get("commandWindows" if os.name == "nt" else "command")
    timeout = handler.get("timeout", 600)
    if handler.get("type") != "command" or not isinstance(command, str):
        raise ActivationError("managed hook handler must be a selected command")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ActivationError("managed hook timeout must be numeric")
    normalized: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": max(timeout, 1),
        "async": False,
    }
    if "statusMessage" in handler:
        normalized["statusMessage"] = handler["statusMessage"]
    identity: dict[str, Any] = {
        "event_name": event_label(event),
        "hooks": [normalized],
    }
    if isinstance(group.get("matcher"), str):
        identity["matcher"] = group["matcher"]
    payload = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def managed_trust_states(config: dict[str, Any], hooks_config: Path) -> dict[str, str]:
    groups = config.get("hooks")
    if groups is None:
        return {}
    if not isinstance(groups, dict):
        raise ActivationError("hook config field 'hooks' must be a JSON object")
    states: dict[str, str] = {}
    for definition in HOOK_DEFINITIONS:
        event = definition.event_name
        existing = groups.get(event, [])
        if not isinstance(existing, list):
            raise ActivationError(f"hook config event must be an array: {event}")
        for group_index, group in enumerate(existing):
            if not is_managed_group(group):
                continue
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ActivationError(f"managed hook group must contain an array: {event}")
            for handler_index, handler in enumerate(group["hooks"]):
                if not is_managed_handler(handler):
                    continue
                if not isinstance(handler, dict):
                    raise ActivationError(f"managed hook handler must be an object: {event}")
                key = (
                    f"{os.fspath(hooks_config)}:{event_label(event)}:"
                    f"{group_index}:{handler_index}"
                )
                states[key] = _trusted_hash(event, group, handler)
    return states


def _read_state(path: Path) -> dict[str, Any] | None:
    require_regular_file(path, "activation state")
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ActivationError(f"activation state is not valid JSON: {path}") from error
    states = value.get("states") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("hooksConfig"), str)
        or not isinstance(value.get("codexConfig"), str)
        or not isinstance(states, dict)
        or any(not isinstance(key, str) or not isinstance(item, str) for key, item in states.items())
    ):
        raise ActivationError(f"activation state has an unsupported shape: {path}")
    return value


def _record(hooks_config: Path, codex_config: Path, states: dict[str, str]) -> dict[str, Any]:
    return {
        "version": 1,
        "hooksConfig": os.fspath(hooks_config),
        "codexConfig": os.fspath(codex_config),
        "states": dict(sorted(states.items())),
    }


def _check_paths(record: dict[str, Any], hooks_config: Path, codex_config: Path) -> None:
    if (
        record["hooksConfig"] != os.fspath(hooks_config)
        or record["codexConfig"] != os.fspath(codex_config)
    ):
        raise ActivationError("activation state belongs to a different config")


def validate_activation_paths(codex_config: Path, data_root: Path, installing: bool) -> None:
    require_regular_file(codex_config, "Codex config")
    if installing:
        require_directory(data_root, "plugin data root", create=True)
    elif data_root.exists() or data_root.is_symlink():
        require_directory(data_root, "plugin data root", create=False)
    if data_root.exists():
        _read_state(data_root / ACTIVATION_STATE_NAME)


def activate_install(
    config: dict[str, Any], hooks_config: Path, codex_config: Path, data_root: Path
) -> bool:
    require_directory(data_root, "plugin data root", create=True)
    state_path = data_root / ACTIVATION_STATE_NAME
    previous = _read_state(state_path)
    if previous is not None:
        _check_paths(previous, hooks_config, codex_config)
    before = read_toml(codex_config)
    desired = managed_trust_states(config, hooks_config)
    prior_states = previous["states"] if previous is not None else {}
    owned = {key: value for key, value in prior_states.items() if desired.get(key) != value}
    after = edit_codex_toml(before, owned, desired, enable_hooks=True)
    if after != before:
        atomic_write(codex_config, after, "Codex config")
    record = _record(hooks_config, codex_config, desired)
    changed = previous != record
    if changed:
        atomic_write(
            state_path,
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            "activation state",
        )
    return after != before or changed


def activate_uninstall(hooks_config: Path, codex_config: Path, data_root: Path) -> bool:
    if not data_root.exists() and not data_root.is_symlink():
        return False
    require_directory(data_root, "plugin data root", create=False)
    state_path = data_root / ACTIVATION_STATE_NAME
    record = _read_state(state_path)
    if record is None:
        return False
    _check_paths(record, hooks_config, codex_config)
    before = read_toml(codex_config)
    after = edit_codex_toml(before, record["states"], {}, enable_hooks=False)
    if after != before:
        atomic_write(codex_config, after, "Codex config")
    require_regular_file(state_path, "activation state")
    state_path.unlink()
    return True

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tomllib

from office_hook_activation import ACTIVATION_STATE_NAME, _read_state, managed_trust_states
from office_hook_registry import RegistryError, managed_group, read_config, require_plugin_root
from office_hook_toml import TomlError, read_toml


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
if os.fspath(HOOKS) not in sys.path:
    sys.path.insert(0, os.fspath(HOOKS))

from office_hook_spec import HOOK_DEFINITIONS
from office_hooks.diagnostics import cleanup_expired_receipt, write_receipt
from office_hooks.state import HookStateError, ensure_ordinary_directory


SAFE_REMEDIATIONS = {
    "config_trust": "repair_trust",
    "state_safety": "repair_private_state",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Office OS hook diagnostics safely.")
    command = parser.add_subparsers(dest="command", required=True)
    check = command.add_parser("check")
    check.add_argument("--plugin-root", required=True)
    check.add_argument("--data-root", required=True)
    check.add_argument("--hooks-config", required=True)
    check.add_argument("--codex-config", required=True)
    check.add_argument("--record-latest", action="store_true")
    return parser.parse_args(argv)


def absolute(value: str) -> Path:
    return Path(os.path.abspath(value))


def trust_is_current(config: dict, plugin_root: Path, data_root: Path) -> bool:
    groups = config.get("hooks")
    if not isinstance(groups, dict):
        return False
    for definition in HOOK_DEFINITIONS:
        existing = groups.get(definition.event_name)
        if not isinstance(existing, list):
            return False
        expected = managed_group(plugin_root, data_root, definition)
        if expected not in existing:
            return False
    return True


def validate_trust(plugin_root: Path, data_root: Path, hooks_config: Path, codex_config: Path) -> None:
    require_plugin_root(plugin_root)
    config = read_config(hooks_config)
    if not trust_is_current(config, plugin_root, data_root):
        raise ValueError("config_trust")
    desired = managed_trust_states(config, hooks_config)
    record = _read_state(data_root / ACTIVATION_STATE_NAME)
    if (
        record is None
        or record["hooksConfig"] != os.fspath(hooks_config)
        or record["codexConfig"] != os.fspath(codex_config)
        or record["states"] != desired
        or not desired
    ):
        raise ValueError("config_trust")
    parsed = tomllib.loads(read_toml(codex_config))
    features = parsed.get("features")
    hooks = parsed.get("hooks")
    states = hooks.get("state") if isinstance(hooks, dict) else None
    if (
        not isinstance(features, dict)
        or features.get("hooks") is not True
        or not isinstance(states, dict)
    ):
        raise ValueError("config_trust")
    if any(
        not isinstance(states.get(key), dict)
        or states[key].get("trusted_hash") != value
        for key, value in desired.items()
    ):
        raise ValueError("config_trust")


def check(args: argparse.Namespace) -> tuple[int, dict[str, object], Path, str | None]:
    plugin_root = absolute(args.plugin_root)
    data_root = absolute(args.data_root)
    hooks_config = absolute(args.hooks_config)
    codex_config = absolute(args.codex_config)
    try:
        if not os.path.lexists(data_root):
            raise HookStateError("Office OS plugin data root is missing.")
        root = ensure_ordinary_directory(data_root, "plugin data root")
        validate_trust(plugin_root, root, hooks_config, codex_config)
    except HookStateError:
        return 2, {"ok": False, "code": "state_safety", "remediation": SAFE_REMEDIATIONS["state_safety"]}, data_root, "state_safety"
    except (OSError, RegistryError, TomlError, ValueError, KeyError, tomllib.TOMLDecodeError):
        return 2, {"ok": False, "code": "config_trust", "remediation": SAFE_REMEDIATIONS["config_trust"]}, data_root, "config_trust"
    return 0, {"ok": True, "code": "ok"}, data_root, None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    status, result, data_root, code = check(args)
    if args.record_latest:
        try:
            root = ensure_ordinary_directory(data_root, "plugin data root", create_parents=True)
            if code is None:
                cleanup_expired_receipt(root)
            else:
                write_receipt(data_root, code, "doctor", "DoctorCheck")
        except HookStateError:
            pass
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from officecli_runtime import OfficeCLIManagerError
from officecli_runtime import current_asset_key
from officecli_runtime import install_runtime
from officecli_runtime import load_lock
from officecli_runtime import managed_binary_path
from officecli_runtime import prune_old_versions
from officecli_runtime import runtime_status
from officecli_runtime import side_effect_free_environment
from officecli_runtime import uninstall_runtime

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


def json_print(value: JsonValue) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Office OS's checksum-pinned OfficeCLI MCP runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="Check the managed runtime without installing it.")
    status.set_defaults(function=lambda _args: runtime_status())
    install = subparsers.add_parser("install", help="Download and verify the pinned runtime once.")
    install.add_argument("--accept-download", action="store_true")
    install.set_defaults(function=lambda args: install_runtime(args.accept_download))
    uninstall = subparsers.add_parser("uninstall", help="Remove only the managed runtime binary.")
    uninstall.set_defaults(function=lambda _args: uninstall_runtime())
    path = subparsers.add_parser("path", help="Print managed-runtime status and path.")
    path.set_defaults(function=lambda _args: runtime_status())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        json_print(args.function(args))
        return 0
    except OfficeCLIManagerError as error:
        json_print({"status": "error", "error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

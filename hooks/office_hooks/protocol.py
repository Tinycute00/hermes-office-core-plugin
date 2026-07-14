from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def read_input() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")


def context_output(event: str, context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }


def plugin_data_context(data_root: Path) -> str:
    return (
        f" Authoritative Office OS PLUGIN_DATA is {os.fspath(data_root)}. "
        "Set PLUGIN_DATA to exactly this path for every office_os.py command; "
        "do not use or invent another data root."
    )

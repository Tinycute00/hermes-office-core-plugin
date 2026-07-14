#!/usr/bin/env python3

from __future__ import annotations

from office_hooks.protocol import configure_stdio, emit, read_input
from office_hooks.tool_guard import handle_tool_guard


configure_stdio()


def main() -> int:
    emit(handle_tool_guard(read_input()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

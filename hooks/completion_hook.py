#!/usr/bin/env python3

from __future__ import annotations

import sys

from office_hooks.completion import handle_completion
from office_hooks.protocol import configure_stdio, read_input
from office_hooks.state import HookStateError


configure_stdio()


def main() -> int:
    try:
        handle_completion(read_input())
    except HookStateError as error:
        sys.stderr.write(f"Office OS hook refused unsafe workspace state: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

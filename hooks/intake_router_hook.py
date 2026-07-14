#!/usr/bin/env python3

from __future__ import annotations

import sys

from office_hooks.intake import handle_user_prompt
from office_hooks.protocol import configure_stdio, emit, read_input
from office_hooks.state import HookStateError


configure_stdio()


def main() -> int:
    payload = read_input()
    if str(payload.get("hook_event_name") or "") != "UserPromptSubmit":
        emit({})
        return 0
    try:
        handle_user_prompt(payload)
    except HookStateError as error:
        sys.stderr.write(f"Office OS hook refused unsafe workspace state: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

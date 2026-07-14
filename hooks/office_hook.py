#!/usr/bin/env python3

from __future__ import annotations

from collections.abc import Callable
import sys
from typing import Any

from office_hook_spec import HOOKS_BY_EVENT, HookDefinition
from office_hooks.completion import handle_completion, handle_stop
from office_hooks.intake import handle_user_prompt
from office_hooks.protocol import configure_stdio, emit, read_input
from office_hooks.session import handle_session_context, handle_session_start
from office_hooks.state import HookStateError


configure_stdio()


HookHandler = Callable[[dict[str, Any]], None]
HANDLERS_BY_CATEGORY: dict[str, HookHandler] = {
    "session_context": handle_session_context,
    "intake_router": handle_user_prompt,
    "completion": handle_completion,
}


def handler_for(definition: HookDefinition) -> HookHandler | None:
    return HANDLERS_BY_CATEGORY.get(definition.category)


def main() -> int:
    payload = read_input()
    event = str(payload.get("hook_event_name") or "")
    definition = HOOKS_BY_EVENT.get(event)
    try:
        if definition is None:
            if event:
                emit({})
            return 0
        handler = handler_for(definition)
        if handler is None:
            emit({})
            return 0
        handler(payload)
    except HookStateError as error:
        sys.stderr.write(f"Office OS hook refused unsafe workspace state: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

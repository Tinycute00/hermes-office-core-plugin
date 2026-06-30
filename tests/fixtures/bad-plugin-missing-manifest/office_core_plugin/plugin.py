from typing import Protocol


class HermesPluginContext(Protocol):
    ...


def register(ctx: HermesPluginContext) -> None:
    _ = ctx

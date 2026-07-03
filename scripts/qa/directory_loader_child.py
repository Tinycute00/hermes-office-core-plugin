# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# Internal child process for validate_directory_loader.py:
#      python scripts/qa/directory_loader_child.py <trusted-repo>
# ──────────────────

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import NoReturn, TypeAlias

JSONValue: TypeAlias = (
    str | int | float | bool | None | Mapping[str, "JSONValue"] | Sequence["JSONValue"]
)
ToolValue: TypeAlias = JSONValue | Callable[..., JSONValue]

EXPECTED_TOOLS = (
    "office_diagnostic",
    "office_plan_workflow",
    "office_preview_operation",
)
EXPECTED_SKILLS = tuple(
    sorted(
        (
            "office-core:office-diagnostic",
            "office-core:office-template-update",
            "office-core:office-data-package",
            "office-core:office-reuse-data",
        )
    )
)


class DirectoryLoaderChildError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise DirectoryLoaderChildError(message)


class FakeHermesContext:
    def __init__(self) -> None:
        self.tools: dict[str, Mapping[str, ToolValue]] = {}
        self.hooks: dict[str, Callable[..., JSONValue]] = {}
        self.commands: dict[str, Callable[..., JSONValue]] = {}
        self.skills: dict[str, Path] = {}

    def register_tool(self, **kwargs: ToolValue) -> None:
        name = kwargs.get("name")
        if not isinstance(name, str):
            fail("registered tool missing string name")
        self.tools[name] = kwargs

    def register_hook(self, hook_name: str, callback: Callable[..., JSONValue]) -> None:
        self.hooks[hook_name] = callback

    def register_command(
        self,
        name: str,
        handler: Callable[..., JSONValue],
        description: str = "",
        args_hint: str = "",
    ) -> None:
        _ = description
        _ = args_hint
        self.commands[name] = handler

    def register_skill(self, name: str, path: Path, description: str = "") -> None:
        _ = description
        self.skills[f"office-core:{name}"] = path


def path_contains_importable_office_core(
    path_entry: str,
    *,
    repo_root: Path,
    plugin_dir: Path,
) -> bool:
    if path_entry == "":
        return True
    try:
        candidate = Path(path_entry).resolve()
    except OSError:
        return False
    return (
        candidate in (repo_root, plugin_dir)
        or repo_root in candidate.parents
        or plugin_dir in candidate.parents
        or candidate.name == "site-packages"
        or (candidate / "office_core_plugin").exists()
        or (candidate / "office_core_plugin.py").exists()
    )


def remove_importable_office_core_paths(repo_root: Path, plugin_dir: Path) -> None:
    sys.path = [
        path_entry
        for path_entry in sys.path
        if not path_contains_importable_office_core(
            path_entry,
            repo_root=repo_root,
            plugin_dir=plugin_dir,
        )
    ]
    sys.meta_path = [
        finder
        for finder in sys.meta_path
        if not (
            type(finder).__module__.startswith("__editable__")
            or getattr(finder, "__module__", "").startswith("__editable__")
        )
    ]
    importlib.invalidate_caches()
    if str(repo_root) in sys.path or str(plugin_dir) in sys.path:
        fail("repo/plugin path remained importable")
    if importlib.util.find_spec("office_core_plugin") is not None:
        fail("office_core_plugin remained importable before isolated load")


def install_stale_package() -> None:
    def stale_register(_ctx: FakeHermesContext) -> None:
        return None

    stale_package = types.ModuleType("office_core_plugin")
    stale_package.__file__ = str(
        Path.cwd() / "site-packages" / "office_core_plugin" / "__init__.py"
    )
    stale_package.register = stale_register
    sys.modules["office_core_plugin"] = stale_package


def load_plugin_module(plugin_dir: Path) -> ModuleType:
    ns_parent = "hermes_plugins"
    ns_pkg = types.ModuleType(ns_parent)
    ns_pkg.__path__ = []
    ns_pkg.__package__ = ns_parent
    sys.modules[ns_parent] = ns_pkg

    module_name = "hermes_plugins.office_core"
    spec = importlib.util.spec_from_file_location(
        module_name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    if spec is None or spec.loader is None:
        fail("could not create plugin module spec")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_dir)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def validate_loaded_module(module: ModuleType, plugin_dir: Path) -> None:
    register = getattr(module, "register", None)
    if not callable(register):
        fail("loaded module register is not callable")
    loaded_package = sys.modules.get("office_core_plugin")
    if loaded_package is None or not hasattr(loaded_package, "__file__"):
        fail("loaded office_core_plugin package missing __file__")
    loaded_package_file = Path(loaded_package.__file__).resolve()
    if not str(loaded_package_file).startswith(str(plugin_dir)):
        fail("loaded office_core_plugin did not come from plugin dir")

    ctx = FakeHermesContext()
    register(ctx)
    if tuple(ctx.tools) != EXPECTED_TOOLS:
        fail("registered tools mismatch")
    if tuple(sorted(ctx.hooks)) != ("post_tool_call",):
        fail("registered hooks mismatch")
    if tuple(sorted(ctx.commands)) != ("office_status",):
        fail("registered commands mismatch")
    if tuple(sorted(ctx.skills)) != EXPECTED_SKILLS:
        fail("registered skills mismatch")
    if not (ctx.tools and ctx.hooks):
        fail("register did not populate tools and hooks")


def main(argv: Sequence[str] = sys.argv) -> int:
    repo_root = Path(argv[1]).resolve(strict=True)
    plugin_dir = repo_root
    remove_importable_office_core_paths(repo_root, plugin_dir)
    install_stale_package()
    module = load_plugin_module(plugin_dir)
    validate_loaded_module(module, plugin_dir)

    print("repo_root_not_on_sys_path=PASS")
    print("plugin_dir_not_on_sys_path=PASS")
    print("loaded_package_from_plugin_dir=PASS")
    print("register_callable=PASS")
    print("register_invoked=PASS")
    print("registered_tools=PASS")
    print("registered_hooks=PASS")
    print("registered_commands=PASS")
    print("registered_skills=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

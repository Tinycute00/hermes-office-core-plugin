#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. With uv:
#      uv run scripts/qa/validate_directory_loader.py --repo .
# 2. With project Python:
#      python scripts/qa/validate_directory_loader.py --repo .
# ──────────────────

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn

if TYPE_CHECKING:
    from collections.abc import Sequence

SPLIT_REPO_ARG_COUNT: Final = 3
INLINE_REPO_ARG_COUNT: Final = 2


class DirectoryLoaderValidationError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise DirectoryLoaderValidationError(message)


def strict_loader_child_code() -> str:
    return textwrap.dedent(
        """
        from __future__ import annotations

        import importlib
        import importlib.util
        import sys
        import types
        from pathlib import Path

        EXPECTED_TOOLS = (
            "office_diagnostic",
            "office_plan_workflow",
            "office_preview_operation",
        )
        EXPECTED_SKILLS = tuple(sorted((
            "office-core:office-diagnostic",
            "office-core:office-template-update",
            "office-core:office-data-package",
            "office-core:office-reuse-data",
        )))

        repo_root = Path(sys.argv[1]).resolve(strict=True)
        plugin_dir = repo_root

        def path_contains_importable_office_core(path_entry: str) -> bool:
            if path_entry == "":
                return True
            try:
                candidate = Path(path_entry).resolve()
            except OSError:
                return False
            return (
                candidate == repo_root
                or candidate == plugin_dir
                or repo_root in candidate.parents
                or plugin_dir in candidate.parents
                or candidate.name == "site-packages"
                or (candidate / "office_core_plugin").exists()
                or (candidate / "office_core_plugin.py").exists()
            )

        sys.path = [
            path_entry
            for path_entry in sys.path
            if not path_contains_importable_office_core(path_entry)
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
        assert str(repo_root) not in sys.path
        assert str(plugin_dir) not in sys.path
        assert importlib.util.find_spec("office_core_plugin") is None

        class FakeHermesContext:
            def __init__(self) -> None:
                self.tools = {}
                self.hooks = {}
                self.commands = {}
                self.skills = {}

            def register_tool(self, **kwargs):
                name = kwargs["name"]
                self.tools[name] = kwargs

            def register_hook(self, hook_name, callback):
                self.hooks[hook_name] = callback

            def register_command(
                self,
                name,
                handler,
                description="",
                args_hint="",
            ):
                _ = description
                _ = args_hint
                self.commands[name] = handler

            def register_skill(self, name, path, description=""):
                _ = description
                self.skills[f"office-core:{name}"] = path

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
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        module.__package__ = module_name
        module.__path__ = [str(plugin_dir)]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        assert callable(module.register)

        ctx = FakeHermesContext()
        module.register(ctx)
        assert tuple(ctx.tools) == EXPECTED_TOOLS
        assert tuple(sorted(ctx.hooks)) == ("post_tool_call",)
        assert tuple(sorted(ctx.commands)) == ("office_status",)
        assert tuple(sorted(ctx.skills)) == EXPECTED_SKILLS
        assert bool(ctx.tools and ctx.hooks)

        print("repo_root_not_on_sys_path=PASS")
        print("plugin_dir_not_on_sys_path=PASS")
        print("register_callable=PASS")
        print("register_invoked=PASS")
        print("registered_tools=PASS")
        print("registered_hooks=PASS")
        print("registered_commands=PASS")
        print("registered_skills=PASS")
        """,
    )


def run_strict_loader(repo: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    _ = env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="office-core-loader-") as temp_dir:
        return subprocess.run(
            [sys.executable, "-c", strict_loader_child_code(), str(repo)],
            cwd=temp_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )


def parse_repo_arg(argv: Sequence[str]) -> Path:
    if len(argv) == SPLIT_REPO_ARG_COUNT and argv[1] == "--repo":
        return Path(argv[2]).resolve(strict=True)
    if len(argv) == INLINE_REPO_ARG_COUNT and argv[1].startswith("--repo="):
        return Path(argv[1].partition("=")[2]).resolve(strict=True)
    fail("usage: validate_directory_loader.py --repo <plugin-repo-root>")


def main(argv: Sequence[str] = sys.argv) -> int:
    repo = parse_repo_arg(argv)
    completed = run_strict_loader(repo)
    print(f"command=<{sys.executable} -c strict_loader_child_code {repo}>")
    print(f"exit_code={completed.returncode}")
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print("stderr:")
        print(completed.stderr.rstrip())
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

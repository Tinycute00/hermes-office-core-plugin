from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from tests.register_contract_helpers import EXPECTED_SKILLS, EXPECTED_TOOLS


def test_directory_plugin_loader_registers_without_sys_path_leakage(
    tmp_path: Path,
) -> None:
    # Given: a plugin checkout selected by the caller and an unrelated process cwd.
    repo_root = _plugin_repo_root()
    run_cwd = tmp_path / "outside-repo"
    run_cwd.mkdir()

    # When: Hermes-style directory loading imports root __init__.py as a package.
    result = _run_strict_loader(repo_root, run_cwd)

    # Then: the load is independent of cwd, repo-root sys.path, and installed packages.
    assert result.returncode == 0, result.stderr
    assert result.stdout == json.dumps({
        "plugin_dir_not_on_sys_path": True,
        "loaded_package_from_plugin_dir": True,
        "register_callable": True,
        "registered_commands": ["office_status"],
        "registered_hooks": ["post_tool_call"],
        "registered_skills": sorted(EXPECTED_SKILLS),
        "registered_tools": list(EXPECTED_TOOLS),
        "repo_root_not_on_sys_path": True,
    }, sort_keys=True) + "\n"


def _plugin_repo_root() -> Path:
    override = os.environ.get("HERMES_OFFICE_CORE_PLUGIN_REPO")
    if override is not None:
        return Path(override).resolve(strict=True)
    return Path(__file__).resolve().parents[1]


def _run_strict_loader(
    repo_root: Path,
    run_cwd: Path,
) -> subprocess.CompletedProcess[str]:
    child_code = textwrap.dedent(
        """
        from __future__ import annotations

        import importlib
        import importlib.util
        import json
        import sys
        import types
        from pathlib import Path

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

        stale_package = types.ModuleType("office_core_plugin")
        stale_package.__file__ = "/tmp/site-packages/office_core_plugin/__init__.py"
        stale_package.register = lambda ctx: None
        sys.modules["office_core_plugin"] = stale_package

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
        loaded_package_file = Path(sys.modules["office_core_plugin"].__file__).resolve()
        assert str(loaded_package_file).startswith(str(plugin_dir))

        ctx = FakeHermesContext()
        module.register(ctx)
        print(
            json.dumps(
                {
                    "plugin_dir_not_on_sys_path": str(plugin_dir) not in sys.path,
                    "loaded_package_from_plugin_dir": str(loaded_package_file).startswith(
                        str(plugin_dir),
                    ),
                    "register_callable": callable(module.register),
                    "registered_commands": sorted(ctx.commands),
                    "registered_hooks": sorted(ctx.hooks),
                    "registered_skills": sorted(ctx.skills),
                    "registered_tools": list(ctx.tools),
                    "repo_root_not_on_sys_path": str(repo_root) not in sys.path,
                },
                sort_keys=True,
            ),
        )
        """,
    )
    env = os.environ.copy()
    _ = env.pop("PYTHONPATH", None)
    return subprocess.run(  # noqa: S603 - fixed interpreter/args for loader isolation.
        [sys.executable, "-c", child_code, str(repo_root)],
        cwd=run_cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

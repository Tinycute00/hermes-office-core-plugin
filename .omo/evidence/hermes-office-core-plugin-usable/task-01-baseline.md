# Task 01 Baseline Evidence

Repository: `/run/media/tiny/0472B4E772B4DF1C/APPs/hermes/hermes-office-core-plugin`

## Git / runtime baseline

- `git status --short --branch`
  ```text
  ## codex/hermes-office-external-plugin...origin/codex/hermes-office-external-plugin
  ```
- `git rev-parse HEAD`
  ```text
  9ab9b21e9831b7c756566d3b2bc608bc473c7944
  ```
- `git branch --show-current`
  ```text
  codex/hermes-office-external-plugin
  ```
- `hermes --version`
  ```text
  Hermes Agent v0.17.0 (2026.6.19)
  Project: /home/tiny/.hermes/hermes-agent
  Python: 3.11.15
  OpenAI SDK: 2.24.0
  ```

## Manifests / entry files

- `plugin.yaml`
  ```text
  name: office-core
  version: 0.1.0
  display_name: Office Core
  description: Standalone Hermes Agent office workflow plugin.
  entrypoint: __init__.py
  ```
- `__init__.py`
  ```text
  from office_core_plugin import register

  __all__ = ["register"]
  ```
- `office_core_plugin/__init__.py`
  ```text
  from .plugin import HermesPluginContext, register

  __all__ = ["HermesPluginContext", "register"]
  ```
- `pyproject.toml`
  ```text
  [project.entry-points."hermes_agent.plugins"]
  office-core = "office_core_plugin:register"

  [tool.setuptools]
  packages = ["office_core_plugin", "office_core_plugin.skills"]
  py-modules = []
  include-package-data = true

  [tool.setuptools.package-data]
  office_core_plugin = ["py.typed", "plugin.yaml", "skills/**/*.md"]
  ```
- `MANIFEST.in`
  ```text
  include LICENSE
  include README.md
  include CHANGELOG.md
  include __init__.py
  include plugin.yaml
  include pyproject.toml
  recursive-include office_core_plugin *.py *.typed *.yaml
  recursive-include office_core_plugin/skills *.md
  recursive-include docs *.md
  recursive-include examples *.md
  ```

## Strict loader failure proof

Command run from `/tmp`:

```bash
python3 - <<'PY'
from __future__ import annotations
import importlib.util
import pathlib
import sys
import traceback

repo = pathlib.Path('/run/media/tiny/0472B4E772B4DF1C/APPs/hermes/hermes-office-core-plugin')
plugin_dir = repo / 'office_core_plugin'
assert str(repo) not in sys.path
assert str(plugin_dir) not in sys.path
sys.path = [p for p in sys.path if p not in {str(repo), str(plugin_dir)}]
print(f'cwd={pathlib.Path.cwd()}')
print(f'repo_on_sys_path={str(repo) in sys.path}')
print(f'plugin_dir_on_sys_path={str(plugin_dir) in sys.path}')
spec = importlib.util.spec_from_file_location(
    'hermes_plugins.office_core',
    repo / '__init__.py',
    submodule_search_locations=[str(plugin_dir)],
)
assert spec is not None
assert spec.loader is not None
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
    print('UNEXPECTED_SUCCESS')
    raise SystemExit(0)
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
PY
```

Exit: nonzero (`ModuleNotFoundError`)

Captured output:

```text
Traceback (most recent call last):
  File "<stdin>", line 24, in <module>
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "/run/media/tiny/0472B4E772B4DF1C/APPs/hermes/hermes-office-core-plugin/__init__.py", line 1, in <module>
    from office_core_plugin import register
ModuleNotFoundError: No module named 'office_core_plugin'
cwd=/tmp
repo_on_sys_path=False
plugin_dir_on_sys_path=False
```

## Post-probe safety check

- `git status --short --branch`
  ```text
  ## codex/hermes-office-external-plugin...origin/codex/hermes-office-external-plugin
  ```

## Blockers

- None after switching from unavailable `python` to `python3`.

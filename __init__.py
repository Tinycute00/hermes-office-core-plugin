import importlib.util
import os
import sys

_plugin_dir = os.path.dirname(os.path.abspath(__file__))  # noqa: PTH100, PTH120
_office_core_plugin_name = "office_core_plugin"
_pkg_dir = os.path.join(_plugin_dir, _office_core_plugin_name)  # noqa: PTH118
_spec = importlib.util.spec_from_file_location(
    _office_core_plugin_name,
    os.path.join(_pkg_dir, "__init__.py"),  # noqa: PTH118
    submodule_search_locations=[_pkg_dir],
)
if _spec is None or _spec.loader is None:
    raise ImportError(_office_core_plugin_name)
_office_core_plugin = importlib.util.module_from_spec(_spec)
sys.modules[_office_core_plugin_name] = _office_core_plugin
_spec.loader.exec_module(_office_core_plugin)
register = _office_core_plugin.register

__all__ = ["register"]

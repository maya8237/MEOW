"""Interactive post-install setup for the streamed MEOW installers."""

from ._runtime import PLUGIN_DIRS_ENV, append_plugin_dir, expand_project_pattern, main

__all__ = [
    "PLUGIN_DIRS_ENV",
    "append_plugin_dir",
    "expand_project_pattern",
    "main",
]

"""Interactive post-install setup for the streamed MEOW installers."""

from typing import TYPE_CHECKING

from meow.project.claude_settings import PLUGIN_DIRS_ENV, append_plugin_dir

if TYPE_CHECKING:
    from ._runtime import expand_project_pattern, main

__all__ = [
    "PLUGIN_DIRS_ENV",
    "append_plugin_dir",
    "expand_project_pattern",
    "main",
]


def __getattr__(name: str):
    # Release tooling uses the stdlib-only installer helpers before MEOW and
    # its dependencies are installed. Load interactive setup only on demand.
    if name in {"expand_project_pattern", "main"}:
        from . import _runtime

        return getattr(_runtime, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Management, Execution & Optimization of Workflows."""

from meow.common.safe_platform import install_safe_platform

# First: later imports (the Claude SDK, logging) may call `platform.system()`.
install_safe_platform()

from meow.infrastructure.console import hide_child_consoles  # ruff: ignore[module-import-not-at-top-of-file] -- after the platform install

hide_child_consoles()

"""Optional host integrations for MEOW."""

from .claude import install_claude_hooks, uninstall_claude_hooks
from .handlers import lint_after_edit

__all__ = [
    "install_claude_hooks",
    "lint_after_edit",
    "uninstall_claude_hooks",
]

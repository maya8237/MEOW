"""Optional host integrations for MEOW."""

from .claude import install_claude_hooks, uninstall_claude_hooks
from .handlers import (
    capture_completed_plan,
    lint_after_edit,
    shaping_ripple,
    validate_plan_stop,
)

__all__ = [
    "capture_completed_plan",
    "install_claude_hooks",
    "lint_after_edit",
    "shaping_ripple",
    "uninstall_claude_hooks",
    "validate_plan_stop",
]

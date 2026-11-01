"""Everything that keeps customizations working in Claude Code, in one place.

Skills and hooks are served to Claude Code by each scope's plugin; agents and
workflows are read by MEOW on every run and need no registration. Project
definitions must also stay trackable by git so teammates receive them.
"""

import json
from pathlib import Path

from meow.project.custom.hook_sync import (
    check_hook_registration,
    sync_hook_plugins,
    sync_personal_hook_plugins,
)
from meow.project.custom.sources import unshared_project_dirs
from meow.project.custom.sync import (
    SYNC_SCOPES,
    check_skill_registration,
    sync_personal_skill_plugins,
    sync_skill_plugins,
)


def check_registration(custom, project_dir: Path, user_dir: Path) -> list[str]:
    """Every problem hiding customizations from Claude Code or teammates."""
    return [
        *check_skill_registration(custom.skills, project_dir, user_dir),
        *check_hook_registration(custom.hooks, project_dir, user_dir),
        *unshared_project_dirs(project_dir),
    ]


def sync_registration(
    custom, project_dir: Path, user_dir: Path, scopes: tuple[str, ...] = SYNC_SCOPES
) -> list[str]:
    """Register skills and hooks at the selected scopes; returns what changed."""
    return [
        *sync_skill_plugins(custom.skills, project_dir, user_dir, scopes),
        *sync_hook_plugins(custom.hooks, project_dir, user_dir, scopes),
    ]


def auto_sync_message(custom, project_dir: Path, user_dir: Path) -> str | None:
    """Sync quietly for this user (Claude Code `SessionStart`); None if all is well.

    Writes MEOW's own generated files and personal settings, never the
    committed `.claude/settings.json` or `.gitignore`; what only a shared
    sync or onboarding can fix is reported instead.
    """
    attempted = [
        *sync_personal_skill_plugins(custom.skills, project_dir, user_dir),
        *sync_personal_hook_plugins(custom.hooks, project_dir, user_dir),
    ]
    remaining = check_registration(custom, project_dir, user_dir)
    changed = [line for line in attempted if line not in remaining]
    lines = []
    if changed:
        lines += ["MEOW updated your custom skills/hooks:", *changed]
        lines.append("Restart Claude Code to load them.")
    if remaining:
        lines += ["MEOW customizations need attention:", *remaining]
    return "\n".join(lines) if lines else None


def hook_output(message: str | None) -> str:
    """`SessionStart` hook JSON that shows `message` to the user."""
    return json.dumps({"systemMessage": message}) if message else ""

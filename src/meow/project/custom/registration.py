"""Everything that keeps customizations working in Claude Code, in one place.

Skills and hooks are served to Claude Code by each scope's plugin; agents and
workflows are read by MEOW on every run and need no registration. Project
definitions must also stay trackable by git so teammates receive them.
"""

import json
from pathlib import Path

from meow.project.custom.hook_sync import check_hook_registration, sync_hook_plugins
from meow.project.custom.sources import unshared_project_dirs
from meow.project.custom.sync import (
    SYNC_SCOPES,
    check_skill_registration,
    sync_skill_plugins,
)

SESSION_START = "SessionStart"


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


def _repair_gitignore(project_dir: Path) -> list[str]:
    """Restore MEOW's ignore boundary in an onboarded project, as any run would."""
    from meow.project.onboarding import CONFIG_RELPATH, repair_boundary

    if not unshared_project_dirs(project_dir):
        return []
    if not (project_dir / CONFIG_RELPATH).is_file() or not repair_boundary(project_dir):
        return []
    return [".gitignore: restored MEOW's rules so project definitions are shared"]


def auto_sync(custom, project_dir: Path, user_dir: Path) -> tuple[list, list]:
    """Fix everything a sync can, scope by scope; returns (changed, remaining).

    One broken scope (say, malformed settings JSON) never stops the others.
    """
    project_dir = Path(project_dir)
    attempted = _repair_gitignore(project_dir)
    for scope in SYNC_SCOPES:
        try:
            attempted += sync_registration(custom, project_dir, user_dir, (scope,))
        except (OSError, ValueError) as exc:
            attempted.append(f"{scope}: {exc}")
    remaining = check_registration(custom, project_dir, user_dir)
    changed = [line for line in attempted if line not in remaining]
    return changed, remaining


def auto_sync_message(custom, project_dir: Path, user_dir: Path) -> str | None:
    """What `meow sync --auto` tells the user; None when all was already well."""
    changed, remaining = auto_sync(custom, project_dir, user_dir)
    lines = []
    if changed:
        lines += ["MEOW updated your custom skills/hooks:", *changed]
        if any(line.startswith(("project", ".gitignore")) for line in changed):
            lines.append(
                "Commit the .meow/, .gitignore, and .claude/settings.json changes."
            )
        lines.append("Restart Claude Code to load them.")
    if remaining:
        lines += ["MEOW customizations need attention:", *remaining]
    return "\n".join(lines) if lines else None


def hook_output(message: str | None, event: str = SESSION_START) -> str:
    """Hook JSON showing `message`; at startup it also asks for a skill re-scan."""
    if not message:
        return ""
    output: dict = {"systemMessage": message}
    if event == SESSION_START:
        output["hookSpecificOutput"] = {
            "hookEventName": SESSION_START,
            "reloadSkills": True,
        }
    return json.dumps(output)

"""Everything that keeps customizations working in Claude Code, in one place.

Skills and hooks are served to Claude Code by each scope's plugin; agents and
workflows are read by MEOW on every run and need no registration. Project
definitions must also stay trackable by git so teammates receive them.
"""

import json
from pathlib import Path

from meow.project.custom.hook_sync import check_hook_registration, sync_hook_plugins
from meow.project.custom.legacy import legacy_layout
from meow.project.custom.sources import unshared_project_dirs
from meow.project.custom.sync import (
    SYNC_SCOPES,
    check_skill_registration,
    sync_skill_plugins,
)

SESSION_START = "SessionStart"


def old_layout(project_dir: Path, apply: bool = False) -> list[str]:
    """What still uses the old marketplace layout; with `apply`, migrate it.

    Run it before loading configuration: a config may name an old folder.
    """
    return legacy_layout(project_dir, apply=apply)


def check_registration(custom, project_dir: Path, user_dir: Path) -> list[str]:
    """Every problem hiding customizations from Claude Code or teammates."""
    return [
        *old_layout(project_dir),
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
    """Restore MEOW's ignore boundary in an onboarded project, as any run would.

    It keeps project definitions shared and personal plugins out of git.
    """
    from meow.project.onboarding import CONFIG_RELPATH, boundary_ok, repair_boundary

    if not (project_dir / CONFIG_RELPATH).is_file() or boundary_ok(project_dir):
        return []
    if not repair_boundary(project_dir):
        return []
    return [".gitignore: restored MEOW's rules (shared definitions, personal plugins)"]


def auto_sync(
    custom, project_dir: Path, user_dir: Path, migrated: list[str]
) -> tuple[list, list]:
    """Fix everything a sync can, scope by scope; returns (changed, remaining).

    One broken scope (say, malformed settings JSON) never stops the others.
    """
    project_dir = Path(project_dir)
    attempted = [*migrated, *_repair_gitignore(project_dir)]
    for scope in SYNC_SCOPES:
        try:
            attempted += sync_registration(custom, project_dir, user_dir, (scope,))
        except (OSError, ValueError) as exc:
            attempted.append(f"{scope}: {exc}")
    remaining = check_registration(custom, project_dir, user_dir)
    changed = [line for line in attempted if line not in remaining]
    return changed, remaining


def auto_sync_message(
    custom, project_dir: Path, user_dir: Path, migrated: list[str] | None = None
) -> str | None:
    """What `meow sync --auto` tells the user; None when all was already well.

    `migrated` lists what `old_layout(..., apply=True)` did before `custom` loaded.
    """
    changed, remaining = auto_sync(custom, project_dir, user_dir, migrated or [])
    lines = []
    if changed:
        lines += ["MEOW updated your custom skills/hooks:", *changed]
        if any(line.startswith(("project", ".gitignore")) for line in changed):
            lines.append(
                "Commit the .claude/skills/meow-project/, .meow/, and .gitignore "
                "changes."
            )
        lines.append("Run /reload-plugins (or restart Claude Code) to load them.")
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

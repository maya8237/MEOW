"""Everything that keeps customizations working in Claude Code, in one place.

Skills and hooks are served to Claude Code by each scope's plugin, and agents that
`standalone.syncs` selects are written as subagents; other agents and workflows are
read by MEOW on every run and need no registration. Project
definitions must also stay trackable by git so teammates receive them.
"""

import json
from pathlib import Path

from meow.project.custom.agent_sync import check_agent_registration, sync_agents
from meow.project.custom.hook_sync import check_hook_registration, sync_hook_plugins
from meow.project.custom.legacy import legacy_layout
from meow.project.custom.sources import unshared_project_dirs
from meow.project.custom.sync import (
    SYNC_SCOPES,
    check_skill_registration,
    sync_skill_plugins,
)
from meow.project.home import is_home_dir

SESSION_START = "SessionStart"


def old_layout(project_dir: Path, apply: bool = False) -> list[str]:
    """What still uses the old marketplace layout; with `apply`, migrate it.

    Run it before loading configuration: a config may name an old folder.
    """
    if is_home_dir(project_dir):
        return []
    return legacy_layout(project_dir, apply=apply)


def check_registration(custom, project_dir: Path, user_dir: Path) -> list[str]:
    """Every problem hiding customizations from Claude Code or teammates."""
    shared = [] if is_home_dir(project_dir) else unshared_project_dirs(project_dir)
    return [
        *old_layout(project_dir),
        *check_skill_registration(custom.skills, project_dir, user_dir),
        *check_hook_registration(custom.hooks, project_dir, user_dir),
        *check_agent_registration(
            custom.catalog, project_dir, user_dir, picks=custom.claude_builtins
        ),
        *shared,
    ]


def sync_registration(
    custom, project_dir: Path, user_dir: Path, scopes: tuple[str, ...] = SYNC_SCOPES
) -> list[str]:
    """Register skills, hooks, and opted-in agents at the selected scopes."""
    return [
        *sync_skill_plugins(custom.skills, project_dir, user_dir, scopes),
        *sync_hook_plugins(custom.hooks, project_dir, user_dir, scopes),
        *sync_agents(
            custom.catalog,
            project_dir,
            user_dir,
            scopes=scopes,
            picks=custom.claude_builtins,
        ),
    ]


def repair_gitignore(project_dir: Path) -> list[str]:
    """Keep personal and linked skill folders ignored and untracked, as any run does.

    It acts once those folders exist; an onboarded project is always repaired.
    """
    from meow.project.onboarding import (
        CONFIG_RELPATH,
        _in_git_repo,
        boundary_ok,
        protect_generated,
        repair_boundary,
    )

    if is_home_dir(project_dir) or not _in_git_repo(project_dir):
        return []  # never write a .gitignore outside a git project
    changed = []
    onboarded = (project_dir / CONFIG_RELPATH).is_file()
    if onboarded and not boundary_ok(project_dir) and repair_boundary(project_dir):
        changed.append(
            ".gitignore: restored MEOW's rules (shared definitions, personal plugins)"
        )
    return [*changed, *protect_generated(project_dir)]


def ignore_problems(project_dir: Path) -> list[str]:
    """What `repair_gitignore` would fix, without changing anything."""
    from meow.project.onboarding import generated_problems

    if is_home_dir(project_dir):
        return []
    return generated_problems(project_dir)


def auto_sync(
    custom, project_dir: Path, user_dir: Path, migrated: list[str]
) -> tuple[list, list]:
    """Fix everything a sync can, scope by scope; returns (changed, remaining).

    One broken scope (say, malformed settings JSON) never stops the others.
    """
    project_dir = Path(project_dir)
    attempted = [*migrated, *repair_gitignore(project_dir)]
    for scope in SYNC_SCOPES:
        try:
            attempted += sync_registration(custom, project_dir, user_dir, (scope,))
        except (OSError, ValueError) as exc:
            attempted.append(f"{scope}: {exc}")
    attempted += repair_gitignore(project_dir)  # folders created just now
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
        lines += ["MEOW updated your custom skills/hooks/agents:", *changed]
        if any(line.startswith(("project", ".gitignore")) for line in changed):
            lines.append(
                "Commit the .claude/skills/, .claude/agents/, .claude/settings.json, "
                ".meow/, and .gitignore changes."
            )
        lines.append(
            "Running sessions pick these up; if one is not listed, run /reload-plugins."
        )
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

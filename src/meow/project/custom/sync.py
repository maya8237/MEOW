"""Make custom skills load in Claude Code, live and in every scope.

Skills from a scope's conventional folder and `[custom].skills` directories are
published as `<name>/` beside the user's own skills (`flat_skills`), which
Claude Code watches, so a new skill needs no restart. Earlier versions served
the user scope through a plugin directory in `~/.claude/settings.json`; that
entry is removed so skills do not load twice.
"""

from pathlib import Path

from meow.project.claude_settings import plugin_dir_registered, remove_plugin_dir
from meow.project.custom.definitions import CustomSkill
from meow.project.custom.flat_skills import (
    FLAT_SCOPES,
    configured_wanted,
    flat_problems,
    publish_native,
)
from meow.project.custom.sources import scope_plugin_dir
from meow.project.home import is_home_dir

SYNC_SCOPES = FLAT_SCOPES

# Sync modes: report only, or fix.
CHECK, SHARED = "check", "shared"
Target = tuple[str, Path, Path]  # scope, project_dir, user_dir


def _user_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def register_scope_plugin(scope: str, project_dir: Path, user_dir: Path) -> None:
    """Publish a scope's own-folder skills so Claude Code loads them now."""
    publish_native(scope, project_dir, user_dir)


def _plugin_dir_leftover(user_dir: Path, mode: str) -> list[str]:
    """Drop the user plugin directories earlier versions registered."""
    settings = _user_settings()
    found = []
    for root in (
        scope_plugin_dir("user", user_dir, user_dir),
        Path(user_dir) / "linked" / "plugin",
    ):
        if settings.is_file() and plugin_dir_registered(settings, root):
            found.append(f"user plugin directory ({root}): no longer needed")
            if mode != CHECK:
                remove_plugin_dir(settings, root)
    return found


def _scope_problems(
    target: Target, skills: tuple[CustomSkill, ...], mode: str
) -> list[str]:
    """Describe (and unless `CHECK`, fix) what keeps a scope's skills from loading."""
    scope, project_dir, user_dir = target
    if scope != "user" and is_home_dir(project_dir):
        return []  # run from home, project and local would write the user's folder
    wanted = configured_wanted(scope, project_dir, skills)
    found = flat_problems(scope, project_dir, wanted, fix=mode != CHECK)
    if scope == "user":
        found += _plugin_dir_leftover(user_dir, mode)
    return found


def check_skill_registration(
    skills: tuple[CustomSkill, ...], project_dir: Path, user_dir: Path
) -> list[str]:
    """Problems that would hide custom skills from Claude Code; changes nothing."""
    problems = []
    for scope in SYNC_SCOPES:
        try:
            problems += _scope_problems((scope, project_dir, user_dir), skills, CHECK)
        except (OSError, ValueError) as exc:
            problems.append(f"{scope} skills: {exc}")
    return problems


def sync_skill_plugins(
    skills: tuple[CustomSkill, ...],
    project_dir: Path,
    user_dir: Path,
    scopes: tuple[str, ...] = SYNC_SCOPES,
) -> list[str]:
    """Publish every selected scope's skills; returns what was fixed."""
    fixed = []
    for scope in scopes:
        fixed += _scope_problems((scope, project_dir, user_dir), skills, SHARED)
    return fixed

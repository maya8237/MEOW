"""Register custom skills with Claude Code so they load as normal plugin skills.

Each scope's conventional `plugin/skills/` folder is a Claude plugin. Skills
from `[custom].skills` directories live anywhere, so they are copied into a
generated `linked` plugin beside it. Both are enabled in the scope's Claude
settings file; Claude Code picks them up on its next launch.
"""

import shutil
from pathlib import Path

from meow.project.claude_settings import (
    append_plugin_dir,
    marketplace_registered,
    plugin_dir_registered,
    register_marketplace,
)
from meow.project.custom.definitions import CustomSkill
from meow.project.custom.plugin import (
    ensure_marketplace,
    ensure_plugin_root,
    plugin_name,
)
from meow.project.custom.sources import (
    git_ignored,
    linked_plugin_dir,
    scope_plugin_dir,
    source_files,
)

SYNC_SCOPES = ("local", "project", "user")
_SETTINGS = {"project": "settings.json", "local": "settings.local.json"}


def _settings_path(scope: str, project_dir: Path) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path(project_dir) / ".claude" / _SETTINGS[scope]


def _root(scope: str, project_dir: Path, user_dir: Path, linked: bool) -> Path:
    where = linked_plugin_dir if linked else scope_plugin_dir
    return where(scope, project_dir, user_dir)


def register_scope_plugin(
    scope: str, project_dir: Path, user_dir: Path, *, linked: bool = False
) -> None:
    """Create a scope's skill plugin and enable it in Claude Code's settings."""
    root = _root(scope, project_dir, user_dir, linked)
    ensure_plugin_root(root, scope, linked=linked)
    settings = _settings_path(scope, project_dir)
    if scope == "user":
        append_plugin_dir(settings, root)
        return
    ensure_marketplace(root.parent, scope, linked=linked)
    register_marketplace(
        settings,
        plugin_name(scope, linked=linked),
        root.parent,
        relative_to=project_dir,
    )


def _registered(scope: str, project_dir: Path, user_dir: Path, linked: bool) -> bool:
    """Whether Claude Code would load the plugin, by any form it honors.

    Project and local settings are both read for the project, so either file
    counts; the user file accepts a plugin directory or an enabled marketplace.
    """
    name = plugin_name(scope, linked=linked)
    root = _root(scope, project_dir, user_dir, linked)
    if scope == "user":
        settings = _settings_path(scope, project_dir)
        return plugin_dir_registered(settings, root) or marketplace_registered(
            settings, name, root.parent
        )
    return any(
        marketplace_registered(
            _settings_path(file, project_dir),
            name,
            root.parent,
            relative_to=project_dir,
        )
        for file in (scope, *(other for other in _SETTINGS if other != scope))
    )


def scope_plugin_registered(scope: str, project_dir: Path, user_dir: Path) -> bool:
    """Whether Claude Code is set to load the scope's own plugin."""
    return _registered(scope, project_dir, user_dir, False)


def linked_skills(
    skills: tuple[CustomSkill, ...], scope: str, project_dir: Path, user_dir: Path
) -> list[CustomSkill]:
    """Winning skills a scope's config entries add, outside its own plugin."""
    native = scope_plugin_dir(scope, project_dir, user_dir) / "skills"
    return [
        skill
        for skill in skills
        if skill.source.scope == scope
        and skill.source.config_file is not None
        and skill.directory.parent.resolve() != native.resolve()
    ]


def _tree(directory: Path, skill: CustomSkill | None = None) -> dict[str, bytes]:
    files = (
        source_files(directory, skill.source) if skill else sorted(directory.rglob("*"))
    )
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in files
        if path.is_file()
    }


def _linked_current(root: Path, skills: list[CustomSkill]) -> bool:
    folder = root / "skills"
    present = (
        {path.name for path in folder.iterdir() if not path.name.startswith(".")}
        if folder.is_dir()
        else set()
    )
    if present != {skill.name for skill in skills}:
        return False
    return all(
        _tree(skill.directory, skill) == _tree(folder / skill.name) for skill in skills
    )


def _build_linked(root: Path, skills: list[CustomSkill]) -> None:
    for skill in skills:
        source_files(skill.directory, skill.source)  # containment checks first
    shutil.rmtree(root / "skills", ignore_errors=True)
    for skill in skills:
        shutil.copytree(skill.directory, root / "skills" / skill.name)


def _plugin_problems(
    target: tuple[str, Path, Path], linked: list[CustomSkill] | None, apply: bool
) -> list[str]:
    """Problems of one plugin: native when `linked` is None, else the linked copy."""
    scope, project_dir, user_dir = target
    is_linked = linked is not None
    root = _root(scope, project_dir, user_dir, is_linked)
    label = f"{scope} {'linked ' if is_linked else ''}skills ({root})"
    if scope == "project" and git_ignored(project_dir, root):
        return [f"{label}: skipped, ignored by git so it would not be shared"]
    found = []
    if is_linked and not _linked_current(root, linked):
        found.append(f"{label}: copies are out of date")
    if not _registered(scope, project_dir, user_dir, is_linked):
        found.append(f"{label}: not registered with Claude Code")
    if apply and found:
        register_scope_plugin(scope, project_dir, user_dir, linked=is_linked)
        if is_linked:
            _build_linked(root, linked)
    return found


def _scope_problems(
    target: tuple[str, Path, Path], skills: tuple[CustomSkill, ...], apply: bool
) -> list[str]:
    """Describe (and with `apply`, fix) what keeps a scope's skills from loading."""
    scope, project_dir, user_dir = target
    found = []
    if (scope_plugin_dir(scope, project_dir, user_dir) / "skills").is_dir():
        found += _plugin_problems(target, None, apply)
    linked = linked_skills(skills, scope, project_dir, user_dir)
    if linked or linked_plugin_dir(scope, project_dir, user_dir).is_dir():
        found += _plugin_problems(target, linked, apply)
    return found


def check_skill_registration(
    skills: tuple[CustomSkill, ...], project_dir: Path, user_dir: Path
) -> list[str]:
    """Problems that would hide custom skills from Claude Code; changes nothing."""
    problems = []
    for scope in SYNC_SCOPES:
        try:
            problems += _scope_problems((scope, project_dir, user_dir), skills, False)
        except (OSError, ValueError) as exc:
            problems.append(f"{scope} skills: {exc}")
    return problems


def sync_skill_plugins(
    skills: tuple[CustomSkill, ...],
    project_dir: Path,
    user_dir: Path,
    scopes: tuple[str, ...] = SYNC_SCOPES,
) -> list[str]:
    """Register every selected scope's skills; returns what was fixed."""
    fixed = []
    for scope in scopes:
        fixed += _scope_problems((scope, project_dir, user_dir), skills, True)
    return fixed

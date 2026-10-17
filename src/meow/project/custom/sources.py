"""Where custom skills, agents, and workflows come from.

Two kinds of source per scope: directories listed in a config layer's
`[custom]` table, and the scope's conventional directories (`.meow/agents/`
and the like), which need no configuration. MEOW's own definitions are the
lowest scope, `builtin`, shipped inside this package.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Every role whose SDK options go through `Agent.options()`.
SKILL_ROLES = frozenset({
    "explorer",
    "planner",
    "generator",
    "reviewer",
    "tester",
    "review_fixer",
    "lint_fixer",
    "docs_updater",
    "issue_fetcher",
    "gitlab_fetcher",
    "github_fetcher",
})

# Roles that hold the Agent tool and can therefore delegate to a custom agent.
AGENT_ROLES = frozenset({"planner", "generator"})

SCOPES = ("local", "project", "user", "builtin")
KINDS = ("skills", "agents", "workflows")
_ENTRY_KEYS = frozenset({"path", "roles"})
# Conventional directories, relative to the project root (local, project) or
# to `~/.meow` (user).
_CONVENTIONAL = {"local": Path(".meow") / "local", "project": Path(".meow")}
_PLUGIN_DIR = "plugin"


@dataclass(frozen=True)
class CustomSource:
    """One directory of custom skills, agents, or workflows."""

    kind: str  # "skills", "agents", or "workflows"
    path: Path
    scope: str
    config_file: Path | None  # None: a conventional or builtin directory
    roles: tuple[str, ...] = ()  # empty: every role the kind allows
    missing: bool = False  # user scope only: the directory is absent, so skipped
    project_dir: Path | None = None  # containment boundary for project contents

    @property
    def delegate(self) -> bool:
        """Agents from a `[custom].agents` entry are offered as subagents."""
        return self.kind == "agents" and self.config_file is not None


def builtin_dir() -> Path:
    """MEOW's shipped agent and workflow definitions."""
    return Path(__file__).resolve().parent / "builtin"


def builtin_sources() -> list[CustomSource]:
    return [
        CustomSource(kind, builtin_dir() / kind, "builtin", None)
        for kind in ("agents", "workflows")
        if (builtin_dir() / kind).is_dir()
    ]


def conventional_sources(project_dir: Path, user_dir: Path) -> list[CustomSource]:
    """Each scope's conventional agent, workflow, and skill directories that exist."""
    return [
        CustomSource(
            kind,
            _resolved_source(
                conventional_dir(scope, kind, project_dir, user_dir),
                scope,
                project_dir,
                "conventional custom directory",
            ),
            scope,
            None,
            project_dir=project_dir if scope == "project" else None,
        )
        for scope in ("local", "project", "user")
        for kind in KINDS
        if conventional_dir(scope, kind, project_dir, user_dir).is_dir()
    ]


def scope_plugin_dir(scope: str, project_dir: Path, user_dir: Path) -> Path:
    """The scope's Claude plugin root; its `skills/` folder holds custom skills."""
    return _scope_base(scope, project_dir, user_dir) / _PLUGIN_DIR


def conventional_dir(scope: str, kind: str, project_dir: Path, user_dir: Path) -> Path:
    """Where `meow ... eject --scope <scope>` puts a definition, or a skill goes."""
    if kind == "skills":
        return scope_plugin_dir(scope, project_dir, user_dir) / "skills"
    return _scope_base(scope, project_dir, user_dir) / kind


def _scope_base(scope: str, project_dir: Path, user_dir: Path) -> Path:
    if scope == "user":
        return Path(user_dir)
    return Path(project_dir) / _CONVENTIONAL[scope]


def git_ignored(project_dir: Path, path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            cwd=project_dir,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _resolve(raw: str, scope: str, base: Path, where: str) -> Path:
    text = raw.replace("\\", "/")
    if scope == "project":
        pure = PurePosixPath(text)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or text.startswith("~")
            or text[1:2] == ":"
        ):
            raise ValueError(
                f"{where}: project-scope path {raw!r} must be relative to the "
                "repository and stay inside it; put personal paths in "
                ".meow/config.local.toml or ~/.meow/config.toml"
            )
    path = Path(raw).expanduser()
    return _resolved_source(
        path if path.is_absolute() else base / path, scope, base, where
    )


def _resolved_source(path: Path, scope: str, base: Path, where: str) -> Path:
    resolved = path.resolve()
    if scope == "project" and not resolved.is_relative_to(base.resolve()):
        raise ValueError(
            f"{where}: project-scope path {str(path)!r} must stay inside "
            "the repository "
            "after resolving filesystem links; put personal paths in "
            ".meow/config.local.toml or ~/.meow/config.toml"
        )
    return resolved


def validate_source_path(path: Path, source: CustomSource) -> Path:
    """Check a discovered definition or asset without changing its logical name."""
    if source.scope == "project":
        _resolved_source(
            path,
            source.scope,
            source.project_dir or source.path,
            "custom project content",
        )
    return path


def source_files(directory: Path, source: CustomSource) -> list[Path]:
    """List skill files with containment checks, following links like copytree."""
    files = []

    def visit(path: Path, ancestors: frozenset[Path]) -> None:
        validate_source_path(path, source)
        if not path.is_dir():
            if path.is_file():
                files.append(path)
            return
        resolved = path.resolve()
        if resolved in ancestors:
            raise ValueError(f"{path}: recursive filesystem link in custom skill")
        for child in path.iterdir():
            visit(child, ancestors | {resolved})

    visit(directory, frozenset())
    return sorted(files)


def _roles(value: object, kind: str, where: str) -> tuple[str, ...]:
    if kind == "workflows":
        raise ValueError(f"{where}: workflow directories take no 'roles'")
    allowed = AGENT_ROLES if kind == "agents" else SKILL_ROLES
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{where}: 'roles' must be a list of role names")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"{where}: unknown role(s) {unknown} for custom {kind}; "
            f"allowed: {sorted(allowed)}"
        )
    return tuple(dict.fromkeys(value))


def _directory(value: object, scope: str, base: Path, where: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must set a non-empty 'path'")
    path = _resolve(value, scope, base, where)
    if scope == "user":
        return path  # a stale personal entry must not break every project
    if not path.is_dir():
        raise ValueError(f"{where}: directory {path} does not exist")
    if scope == "project" and git_ignored(base, path):
        raise ValueError(
            f"{where}: {value!r} is ignored by git, so it would not be "
            "shared with the project; un-ignore it or declare it in "
            ".meow/config.local.toml instead"
        )
    return path


def _entry(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    raw: object, kind: str, scope: str, base: Path, config_file: Path, position: int
) -> CustomSource:
    where = f"{config_file}: [custom].{kind} entry {position}"
    if isinstance(raw, str):
        raw = {"path": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a path string or a table with 'path'")
    unknown = sorted(set(raw) - _ENTRY_KEYS)
    if unknown:
        raise ValueError(f"{where} has unknown key(s) {unknown}")
    path = _directory(raw.get("path"), scope, base, where)
    roles = _roles(raw["roles"], kind, where) if "roles" in raw else ()
    missing = scope == "user" and not path.is_dir()
    return CustomSource(
        kind,
        path,
        scope,
        config_file,
        roles,
        missing,
        project_dir=base if scope == "project" else None,
    )


def parse_sources(
    table: object, scope: str, base: Path, config_file: Path
) -> list[CustomSource]:
    """Validate one config layer's `[custom]` table.

    Relative paths resolve from `base`: the project root for the project and
    local scopes, `~/.meow` for the user scope. Project-scope paths must stay
    inside the repository and must not be git-ignored.
    """
    if table is None:
        return []
    if not isinstance(table, dict):
        raise ValueError(f"{config_file}: [custom] must be a table")
    unknown = sorted(set(table) - set(KINDS))
    if unknown:
        raise ValueError(f"{config_file}: [custom] has unknown key(s) {unknown}")
    sources = []
    for kind in KINDS:
        entries = table.get(kind, [])
        if not isinstance(entries, list):
            raise ValueError(f"{config_file}: [custom].{kind} must be a list")
        sources.extend(
            _entry(raw, kind, scope, base, config_file, position)
            for position, raw in enumerate(entries, 1)
        )
    return sources

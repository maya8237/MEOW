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


@dataclass(frozen=True)
class CustomSource:
    """One directory of custom skills, agents, or workflows."""

    kind: str  # "skills", "agents", or "workflows"
    path: Path
    scope: str
    config_file: Path | None  # None: a conventional or builtin directory
    roles: tuple[str, ...] = ()  # empty: every role the kind allows
    missing: bool = False  # user scope only: the directory is absent, so skipped

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
    """Each scope's conventional agent and workflow directories that exist."""
    bases = {
        "local": Path(project_dir) / _CONVENTIONAL["local"],
        "project": Path(project_dir) / _CONVENTIONAL["project"],
        "user": Path(user_dir),
    }
    return [
        CustomSource(kind, (base / kind).resolve(), scope, None)
        for scope, base in bases.items()
        for kind in ("agents", "workflows")
        if (base / kind).is_dir()
    ]


def conventional_dir(scope: str, kind: str, project_dir: Path, user_dir: Path) -> Path:
    """Where `meow ... eject --scope <scope>` puts a definition."""
    if scope == "user":
        return Path(user_dir) / kind
    return Path(project_dir) / _CONVENTIONAL[scope] / kind


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
    return (path if path.is_absolute() else base / path).resolve()


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
    return CustomSource(kind, path, scope, config_file, roles, missing)


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

"""Deterministic first-run onboarding for a project.

Sets up the shared MEOW boundary without any agent or prompt: the `.gitignore`
rules that keep `.meow/config.toml` trackable while ignoring runtime state, and
a minimal `.meow/config.toml`. Optional integrations are never configured here;
the `/onboard` skill offers those. Only the filesystem and `git` are touched.
"""

import json
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from meow.hooks.claude import inspect_claude_hooks
from meow.project.config import config_paths

BOUNDARY = (".meow/*", "!.meow/", "!.meow/config.toml")

CONFIG_RELPATH = ".meow/config.toml"

SKIPPED_FEATURES = (
    "Jira",
    "GitLab",
    "GitHub",
    "Tester mode",
    "Claude hooks",
    "Worktree setup",
)

_BROAD = frozenset({".meow", ".meow/", "/.meow", "/.meow/"})
_STALE = _BROAD | frozenset(BOUNDARY)
_EXPECTED_IGNORED = {
    ".meow/config.toml": False,
    ".meow/config.local.toml": True,
    ".meow/runs/x.json": True,
}


@dataclass(frozen=True)
class OnboardingReport:
    """What one onboarding pass changed and what it deliberately left alone."""

    files: tuple[str, ...]
    lint: str | None
    skipped: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "files": list(self.files),
            "lint": self.lint,
            "skipped": list(self.skipped),
            "error": self.error,
        }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:
        return None


def _in_git_repo(root: Path) -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    return result is not None and result.returncode == 0


def _ignored(root: Path, relpath: str) -> bool | None:
    result = _git(root, "check-ignore", "-q", relpath)
    if result is None or result.returncode not in {0, 1}:
        return None
    return result.returncode == 0


def _text_boundary_ok(root: Path) -> bool:
    path = root / ".gitignore"
    if not path.is_file():
        return False
    rules = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if BOUNDARY[0] not in rules:
        return False
    start = len(rules) - 1 - rules[::-1].index(BOUNDARY[0])
    if tuple(rules[start : start + len(BOUNDARY)]) != BOUNDARY:
        return False
    return not _BROAD & set(rules[start + len(BOUNDARY) :])


def _git_boundary_ok(root: Path) -> bool:
    return all(
        _ignored(root, rel) is want for rel, want in _EXPECTED_IGNORED.items()
    )


def boundary_ok(root: Path) -> bool:
    """True when the ignore boundary is present and (inside git) verified."""
    root = Path(root)
    if not _text_boundary_ok(root):
        return False
    return _git_boundary_ok(root) if _in_git_repo(root) else True


def repair_boundary(root: Path) -> bool:
    """Make `.gitignore` carry the ordered boundary; True if the file changed."""
    root = Path(root)
    if _text_boundary_ok(root):
        return False
    path = root / ".gitignore"
    raw = path.read_bytes().decode("utf-8") if path.is_file() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    kept = [line for line in raw.splitlines() if line.strip() not in _STALE]
    path.write_bytes((newline.join([*kept, *BOUNDARY]) + newline).encode("utf-8"))
    return True


def _has_ruff(root: Path) -> bool:
    if (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file():
        return True
    pyproject = root / "pyproject.toml"
    return pyproject.is_file() and "[tool.ruff" in pyproject.read_text(
        encoding="utf-8", errors="replace"
    )


def _has_eslint(root: Path) -> bool:
    if any(root.glob("eslint.config.*")) or any(root.glob(".eslintrc*")):
        return True
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    groups = (package.get("dependencies"), package.get("devDependencies"))
    return any(isinstance(group, dict) and "eslint" in group for group in groups)


def detect_lint(root: Path) -> tuple[str, str] | None:
    """Return the project's established lint (command, fix flag), if any."""
    root = Path(root)
    if _has_ruff(root):
        return ("python -m ruff check", "--fix")
    if _has_eslint(root):
        return ("npx eslint", "--fix")
    return None


def is_onboarded(root: Path) -> bool:
    """A project is onboarded with a shared config and a correct boundary."""
    root = Path(root)
    return (root / CONFIG_RELPATH).is_file() and boundary_ok(root)


def _config_text(lint: tuple[str, str] | None, existing: set[str]) -> str:
    """Minimal shared config, leaving out anything local/user config already sets."""
    lines = [
        "# Created automatically by meow on the first run.",
        "# See templates/meow-config.toml.example for every option.",
    ]
    if "max_rounds" not in existing:
        lines += ["", "max_rounds = 8"]
    if "docs_dir" not in existing:
        lines += ["", 'docs_dir = ".meow/plans"']
    if lint and "lint" not in existing:
        lines += ["", "[[lint]]", f'command = "{lint[0]}"', f'fix_flag = "{lint[1]}"']
    return "\n".join(lines) + "\n"


def _verify(root: Path) -> str | None:
    if not _git_boundary_ok(root):
        return ".meow ignore boundary failed git check-ignore verification"
    return None


def _apply(
    root: Path, lint: tuple[str, str] | None, files: list[str], *, write_config: bool
) -> str | None:
    if not _in_git_repo(root):
        return "not inside a git repository; ignore boundary could not be verified"
    if repair_boundary(root):
        files.append(".gitignore")
    error = _verify(root)
    config = root / CONFIG_RELPATH
    if error is None and write_config and not config.is_file():
        existing = {key for table in _config_tables(root) for key in table}
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(_config_text(lint, existing), encoding="utf-8")
        files.append(CONFIG_RELPATH)
    return error


def onboard_project(root: Path, *, write_config: bool = True) -> OnboardingReport:
    """Repair the boundary, then write a minimal config. Never overwrites."""
    root = Path(root)
    lint = detect_lint(root)
    files: list[str] = []
    try:
        error = _apply(root, lint, files, write_config=write_config)
    except OSError as exc:
        error = str(exc)
    return OnboardingReport(
        tuple(files), lint[0] if lint else None, SKIPPED_FEATURES, error
    )


def onboard_if_needed(active_dir: Path, config_dir: Path) -> dict | None:
    """Onboard `active_dir` unless the project is already onboarded.

    `config_dir` is the checkout MEOW reads config from (the main checkout for a
    linked worktree). A config already there is never duplicated, so only the
    ignore boundary is repaired in that case. Returns the report as a dict
    (with `error` set on failure), or None when nothing needed doing.
    """
    active_dir, config_dir = Path(active_dir), Path(config_dir)
    if is_onboarded(config_dir) or is_onboarded(active_dir):
        return None
    write_config = not (config_dir / CONFIG_RELPATH).is_file()
    try:
        return onboard_project(active_dir, write_config=write_config).to_dict()
    except (OSError, ValueError, RuntimeError) as exc:
        return {"files": [], "lint": None, "skipped": [], "error": str(exc)}


_GUIDE = "docs/INTEGRATIONS.md"
_GAPS: dict[str, tuple[tuple[str, ...], str]] = {
    "jira": (
        ("jira",),
        f"Configure [jira] and [jira.mcp] ({_GUIDE}), run `meow native verify`, "
        "then `meow run --jira ISSUE-KEY`.",
    ),
    "gitlab": (
        ("gitlab",),
        f"Configure [gitlab.mcp] ({_GUIDE}), then `meow review --gitlab <url>`.",
    ),
    "github": (
        ("github",),
        f"Configure [github.mcp] ({_GUIDE}), then `meow review --github <url>`.",
    ),
    "tester_tests": (
        ("tester", "tests"),
        "Add [[tester.tests]] commands, verify with `meow native verify`, "
        "then run with `--test`.",
    ),
    "tester_mcp": (
        ("tester", "mcp"),
        "Add [[tester.mcp]] launchers for the tester role, then `meow native verify`.",
    ),
    "tester_browser": (
        ("tester", "browser"),
        f"Add [tester.browser] ({_GUIDE}) for browser-driven checks.",
    ),
    "build": (("build",), "Add [[build]] commands that must pass before delivery."),
    "worktree_setup": (
        ("worktree_setup",),
        "Add [worktree_setup] copy/commands, verified in a disposable worktree.",
    ),
    "permissions": (
        ("permissions",),
        "Add [permissions] rules to constrain what roles may run.",
    ),
    "agent_skills": (
        ("agent_skills",),
        "Add [agent_skills] to give roles extra installed skills.",
    ),
}


def _nonempty(value: object) -> bool:
    if isinstance(value, dict):
        return any(_nonempty(item) for item in value.values())
    return bool(value)


def _present(tables: list[dict], path: tuple[str, ...]) -> bool:
    for table in tables:
        value: object = table
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if _nonempty(value):
            return True
    return False


def _config_tables(root: Path) -> list[dict]:
    tables = []
    for path in config_paths(root):
        try:
            tables.append(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return tables


def feature_gaps(root: Path) -> dict[str, dict]:
    """Report which optional capabilities the project has configured.

    Only key presence is read from config files; values (which may hold
    credentials) are never copied into the result.
    """
    root = Path(root)
    tables = _config_tables(root)
    gaps = {
        key: {"configured": _present(tables, path), "enable": enable}
        for key, (path, enable) in _GAPS.items()
    }
    hooks = inspect_claude_hooks(root)
    gaps["hooks"] = {
        "configured": any(v in {"active", "modified"} for v in hooks.values()),
        "enable": "Review and install the Claude hooks (docs/INTEGRATIONS.md).",
    }
    gaps["architecture_doc"] = {
        "configured": (root / "docs" / "ARCHITECTURE.md").is_file(),
        "enable": "Run `meow native knowledge-audit`, then create the accepted doc.",
    }
    return gaps

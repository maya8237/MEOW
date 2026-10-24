"""Repeatable base onboarding and read-only feature discovery for a project.

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

from meow.infrastructure.worktree import default_target_branch
from meow.project.config import _merge_config, config_paths
from meow.project.migration import migrate_legacy_config
from meow.project.repos import remember

BOUNDARY = (
    ".meow/*",
    "!.meow/",
    "!.meow/config.toml",
    "!.meow/skills/",
    "!.meow/plugin/",
    "!.meow/.claude-plugin/",
    "!.meow/agents/",
    "!.meow/workflows/",
    ".claude/settings.local.json",
)

CONFIG_RELPATH = ".meow/config.toml"
LOCAL_CONFIG_RELPATH = ".meow/config.local.toml"

SKIPPED_FEATURES = (
    "Jira",
    "GitLab",
    "GitHub",
    "Tester mode",
    "Worktree setup",
)

_BROAD = frozenset({".meow", ".meow/", "/.meow", "/.meow/"})
_STALE = _BROAD | frozenset(BOUNDARY)
_EXPECTED_IGNORED = {
    ".meow/config.toml": False,
    ".meow/config.local.toml": True,
    ".meow/skills/x/SKILL.md": False,
    ".meow/plugin/skills/x/SKILL.md": False,
    ".meow/.claude-plugin/marketplace.json": False,
    ".meow/local/plugin/skills/x/SKILL.md": True,
    ".meow/agents/x.toml": False,
    ".meow/workflows/x.toml": False,
    ".meow/runs/x.json": True,
    ".claude/settings.local.json": True,
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
    return all(_ignored(root, rel) is want for rel, want in _EXPECTED_IGNORED.items())


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
    if not isinstance(package, dict):
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


def _config_text(
    lint: tuple[str, str] | None, existing: set[str], target: str | None
) -> str:
    """Minimal shared config, leaving out anything local/user config already sets."""
    lines = [
        "# Created automatically by meow on the first run.",
        "# This shared file is safe to commit; keep private values in the local",
        "# config or environment variables. See templates/meow-config.toml.example",
        "# for every option and docs/INTEGRATIONS.md for feature setup.",
    ]
    if "max_rounds" not in existing:
        lines += [
            "",
            "# Maximum generator/reviewer rounds before MEOW pauses for review.",
            "max_rounds = 8",
        ]
    if "docs_dir" not in existing:
        lines += [
            "",
            "# Directory for MEOW-generated plans, reviews, and evidence.",
            'docs_dir = ".meow/plans"',
        ]
    if lint and "lint" not in existing:
        lines += [
            "",
            "# Detected lint command; MEOW uses this for per-file and gate checks.",
            "[[lint]]",
            f'command = "{lint[0]}"',
            f'fix_flag = "{lint[1]}"',
        ]
    if target is not None:
        lines += [
            "",
            "# Default PR/MR target; override per run with --target BRANCH.",
            "[delivery]",
            f'target_branch = "{target}"',
        ]
    return "\n".join(lines) + "\n"


def _local_config_text() -> str:
    """Documentation-only local config template; every setting stays commented."""
    return "\n".join([
        "# Optional machine- and user-specific MEOW settings for this project.",
        "# This file is ignored by Git and overrides .meow/config.toml locally.",
        "# Keep credentials in environment variables or this ignored file,",
        "# never in the shared project config.",
        "#",
        "# Examples (uncomment only what you need):",
        "# [agent_skills]",
        '# default = ["my-installed-skill"]',
        "# [custom]",
        '# skills = ["~/my-meow/skills"]',
        '# agents = ["~/my-meow/agents"]',
        "# [tester.mcp.env]",
        '# TOKEN = "$TOKEN"',
        "#",
        "# See templates/meow-config.toml.example and docs/INTEGRATIONS.md.",
        "",
    ])


def _verify(root: Path) -> str | None:
    if not _git_boundary_ok(root):
        return ".meow ignore boundary failed git check-ignore verification"
    return None


def _ignore_python_metadata(root: Path) -> None:
    """Keep editable-install artifacts out of commits without a tracked edit."""
    if not any((root / name).is_file() for name in ("pyproject.toml", "setup.py")):
        return
    result = _git(root, "rev-parse", "--git-path", "info/exclude")
    if result is None or result.returncode:
        return
    exclude = root / result.stdout.strip()
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if "*.egg-info/" not in existing.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(f"{existing}{prefix}*.egg-info/\n", encoding="utf-8")


def _apply(
    root: Path, lint: tuple[str, str] | None, files: list[str], *, write_config: bool
) -> str | None:
    if not _in_git_repo(root):
        return "not inside a git repository; ignore boundary could not be verified"
    _ignore_python_metadata(root)
    if repair_boundary(root):
        files.append(".gitignore")
    error = _verify(root)
    config = root / CONFIG_RELPATH
    if error is None and write_config and migrate_legacy_config(root):
        files.append(CONFIG_RELPATH)
    if error is None and write_config and not config.is_file():
        tables = _config_tables(root)
        existing = {key for table in tables for key in table}
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            _config_text(
                lint,
                existing,
                None
                if any(
                    _present(table, ("delivery", "target_branch")) for table in tables
                )
                else default_target_branch(root),
            ),
            encoding="utf-8",
        )
        files.append(CONFIG_RELPATH)
    local = root / LOCAL_CONFIG_RELPATH
    if error is None and not local.is_file():
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(_local_config_text(), encoding="utf-8")
        files.append(LOCAL_CONFIG_RELPATH)
    return error


def onboard_project(root: Path, *, write_config: bool = True) -> OnboardingReport:
    """Refresh the boundary and missing configs; preserve existing config files."""
    root = Path(root)
    lint = detect_lint(root)
    files: list[str] = []
    try:
        error = _apply(root, lint, files, write_config=write_config)
    except (OSError, ValueError) as exc:
        error = str(exc)
    return OnboardingReport(
        tuple(files), lint[0] if lint else None, SKIPPED_FEATURES, error
    )


def onboard_if_needed(active_dir: Path, config_dir: Path) -> dict | None:
    """Onboard the config checkout without adding tracked edits to a task branch.

    `config_dir` is the checkout MEOW reads config from (the main checkout for a
    linked worktree). A config already there is never duplicated. Returns a dict
    (with `error` set on failure), or None when nothing needed doing.
    """
    active_dir, config_dir = Path(active_dir), Path(config_dir)
    if is_onboarded(config_dir) or is_onboarded(active_dir):
        remember(config_dir)
        return None
    write_config = not (config_dir / CONFIG_RELPATH).is_file()
    try:
        report = onboard_project(config_dir, write_config=write_config)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"files": [], "lint": None, "skipped": [], "error": str(exc)}
    if report.error is None:
        remember(config_dir)
    return report.to_dict()


_GUIDE = "docs/INTEGRATIONS.md"
# Add optional capabilities here when introducing them, so installer and skill
# reruns discover the same current options without storing an onboarding version.
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
        "and note that configured tests enable Tester mode on every run.",
    ),
    "tester_write_tests": (
        ("tester", "write_tests"),
        "Set write_tests = true under [tester] so the tester adds new tests "
        "for the change (off by default).",
    ),
    "tester_mcp": (
        ("tester", "mcp"),
        "Add [[tester.mcp]] launchers for the tester role, then `meow native verify`.",
    ),
    "tester_browser": (
        ("tester", "browser"),
        f"Add [tester.browser] ({_GUIDE}) for browser-driven checks.",
    ),
    "tester_dev_server": (
        ("tester", "dev_server"),
        f"Add [[tester.dev_server]] ({_GUIDE}), then verify accepted server commands.",
    ),
    "build": (("build",), "Add [[build]] commands that must pass before delivery."),
    "lint": (
        ("lint",),
        "Add [[lint]] using the project's established command and real fix flag.",
    ),
    "models": (("models",), f"Configure role-specific [models] ({_GUIDE})."),
    "workflow": (
        ("workflow",),
        "Select [workflow].default or use `/meow:customize` (docs/WORKFLOWS.md).",
    ),
    "orchestrator": (
        ("orchestrator",),
        "Configure [orchestrator] for multi-sprint runs (docs/ORCHESTRATION.md).",
    ),
    "delivery": (
        ("delivery", "target_branch"),
        "Choose [delivery].target_branch (existing dev, otherwise master by default); "
        "--target overrides it per run.",
    ),
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
    "custom": (
        ("custom",),
        "Add [custom] skill and agent directories (`/meow:customize`).",
    ),
}


def _nonempty(value: object) -> bool:
    if isinstance(value, dict):
        return any(_nonempty(item) for item in value.values())
    return bool(value)


def _present(table: dict, path: tuple[str, ...]) -> bool:
    value: object = table
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return _nonempty(value)


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

    Merge explicit settings with normal config priority, including appended
    skills. Presence is not verification; secret values never enter the result.
    """
    root = Path(root)
    tables = _config_tables(root)
    config: dict = {}
    for table in reversed(tables):
        config = _merge_config(config, table)
    gaps = {
        key: {"configured": _present(config, path), "enable": enable}
        for key, (path, enable) in _GAPS.items()
    }
    # Runtime accumulates custom sources from every layer before merging config.
    gaps["custom"]["configured"] = any(_present(table, ("custom",)) for table in tables)
    gaps["architecture_doc"] = {
        "configured": (root / "docs" / "ARCHITECTURE.md").is_file(),
        "enable": "Run `meow native knowledge-audit`, then create the accepted doc.",
    }
    return gaps

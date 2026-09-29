"""
meow/config.py

Lint-command modeling and .harness.toml loading -- the project-specific
values every role reads through a `Sprint`, kept separate from the agent
wiring and orchestration that consume them.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback -- pip install tomli

CONFIG_FILENAME = ".harness.toml"
DEFAULT_FIX_FLAG = "--fix"
LINT_ENTRY_KEYS = frozenset({"command", "fix_flag", "per_file", "gate"})

DEFAULT_CONFIG = {
    "lint_command": None,           # legacy single-command form
    "lint_fix_flag": DEFAULT_FIX_FLAG,
    "max_rounds": 8,
    "lint_timeout": 60,             # seconds before a per-file lint command is killed

    "docs_dir": "docs/exec-plans/active",
    "models": {
        "explorer": "haiku",
        "planner": None,            # None = engine default
        "generator": None,
        "reviewer": None,
        "issue_fetcher": None,
        "gitlab_fetcher": None,
    },
}


@dataclass(frozen=True)
class LintCommand:
    """One lint command a project has configured.

    A `per_file` command runs as the generator's post-edit hook, on the single
    file just written. A `gate` command runs project-wide for the evaluator and
    its failure is a sprint FAIL; a non-gate command is non-blocking, which is
    what a whole-project analyzer needs when it is expected to report findings on
    pre-existing code.
    """

    command: str
    fix_flag: str | None = None
    per_file: bool = True
    gate: bool = True

    def argv(self) -> list[str]:
        """The command as argv, check-only, program resolved on PATH."""
        argv = self.command.split()
        if not argv:
            raise ValueError(f"{CONFIG_FILENAME}: lint command is empty.")
        # Windows will not exec a .cmd shim (npx, eslint and oxlint all ship
        # as one) from a bare argv, so look the program up the way a shell
        # would. Falls back to the original name so a genuinely missing
        # program still surfaces as the OS error rather than being hidden.
        return [shutil.which(argv[0]) or argv[0], *argv[1:]]

    def argv_for_file(self, file_path: str) -> list[str]:
        """The command as argv for one file, auto-fixing where supported."""
        argv = self.argv()
        if self.fix_flag:
            argv.append(self.fix_flag)
        argv.append(file_path)
        return argv


def _lint_entry(raw: object, position: int) -> LintCommand:
    """Validate one [[lint]] table from the config file."""
    if not isinstance(raw, dict) or not raw.get("command"):
        raise ValueError(
            f"{CONFIG_FILENAME}: [[lint]] entry {position} must set "
            "'command' (e.g. command = \"npx oxlint\")."
        )
    unknown = sorted(set(raw) - LINT_ENTRY_KEYS)
    if unknown:
        raise ValueError(
            f"{CONFIG_FILENAME}: [[lint]] entry {position} has unknown "
            f"key(s) {unknown}. Allowed: {sorted(LINT_ENTRY_KEYS)}."
        )
    return LintCommand(
        command=raw["command"],
        fix_flag=raw.get("fix_flag"),
        per_file=bool(raw.get("per_file", True)),
        gate=bool(raw.get("gate", True)),
    )


def _normalize_lint_commands(user_config: dict) -> list[LintCommand]:
    """Collapse both config forms into one list, in configured order.

    Any number of commands is allowed. The list form is a TOML array of
    tables, each with its own fix flag and its own role:

        [[lint]]
        command = "npx oxlint"
        fix_flag = "--fix"

        [[lint]]
        command = "npx fallow"
        per_file = false          # project-wide only, never per file
        gate = false              # non-blocking: failure is not a sprint FAIL

    The older single-command form still works and becomes one entry:

        lint_command = "ruff check"
        lint_fix_flag = "--fix"
    """
    entries = []

    legacy = user_config.get("lint_command")
    if legacy:
        entries.append(
            LintCommand(
                command=legacy,
                fix_flag=user_config.get("lint_fix_flag", DEFAULT_FIX_FLAG),
            )
        )

    raw_entries = user_config.get("lint", [])
    if not isinstance(raw_entries, list):
        raise ValueError(
            f"{CONFIG_FILENAME}: 'lint' must be a list of [[lint]] tables."
        )
    entries.extend(
        _lint_entry(raw, position)
        for position, raw in enumerate(raw_entries, start=1)
    )

    if not entries:
        raise ValueError(
            f"{CONFIG_FILENAME} must define at least one lint command -- "
            "either a [[lint]] entry with a 'command' key, or the "
            "single-command form lint_command = \"ruff check\"."
        )

    return entries


def load_config(working_dir: Path) -> dict:
    config_path = working_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {working_dir}. "
            "Create one before running the harness -- see the harness "
            "repo's README for the required fields."
        )

    with open(config_path, "rb") as f:
        user_config = tomllib.load(f)

    config = {**DEFAULT_CONFIG, **user_config}
    config["models"] = {**DEFAULT_CONFIG["models"], **user_config.get("models", {})}
    config["lint"] = _normalize_lint_commands(user_config)

    return config

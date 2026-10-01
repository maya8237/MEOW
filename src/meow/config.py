"""
meow/config.py

Lint-command modeling and .harness.toml loading -- the project-specific
values every role reads through a `Sprint`, kept separate from the agent
wiring and orchestration that consume them.
"""

import platform
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
        "lint_fixer": None,
        "review_fixer": None,
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


_WINDOWS_SCRIPT_EXTENSIONS = (".bat", ".cmd")
_WINDOWS_ONLY_PROGRAMS = frozenset({"cmd", "cmd.exe", "powershell", "powershell.exe"})
_UNIX_SCRIPT_EXTENSION = ".sh"
_UNIX_ONLY_PROGRAMS = frozenset({"bash", "sh", "zsh"})
_WSL_LAUNCHERS = frozenset({"wsl", "wsl.exe"})


def _program_name(command: str) -> str:
    """The first token of a lint command, lowercased and path-stripped."""
    first = command.split()[0] if command.split() else ""
    return PurePosixPath(first.replace("\\", "/")).name.lower()


def _os_mismatch(command: str, system: str) -> str | None:
    """None if `command` looks fine to run on `system`; otherwise why not.

    Only catches an explicit, unambiguous marker: a .bat/.cmd/.sh script
    name, or cmd.exe/powershell.exe/bash/sh/zsh invoked directly. `pwsh`
    (PowerShell 7+/Core) is deliberately not included here -- unlike
    `powershell.exe` (Windows PowerShell 5.1), it's genuinely cross-platform,
    so its presence says nothing about which OS the command expects.

    This does NOT check whether the program resolves on PATH at all -- that
    is "not installed yet", a normal, expected condition the lint run itself
    already reports clearly when it happens, not an OS mismatch. There's no
    reliable way to tell "wrong OS" from "not installed yet" for a bare
    program name with none of these markers (ruff, eslint, npx ...,
    golangci-lint, ...), so this check doesn't try -- it only fires on a
    marker that could never be satisfied by installing something on the
    current OS.

    The one deliberate exception to that rule: `bash`/`sh`/`zsh` invoked
    explicitly (e.g. `bash scripts/lint.sh`) are only flagged on Windows if
    none of them resolve on PATH. Git for Windows (an extremely common
    install -- it ships the `git` most Windows developers already have) puts
    a real, working `bash.exe` on PATH, and a project that invokes it
    explicitly this way genuinely runs fine there; checking PATH here isn't
    "is this specific tool installed yet" (project-specific, genuinely
    ambiguous) but "does any POSIX shell interpreter exist on this machine
    at all" (a one-time environment fact, not per-tool). A bare `.sh`
    filename with no explicit interpreter doesn't get this exception, even
    with Git Bash on PATH: Windows has no shebang support, so it can't exec
    a `.sh` file directly the way it execs a `.bat` or `.exe` -- it needs an
    explicit `bash`/`sh` in front of it either way.
    """
    program = _program_name(command)
    if program in _WSL_LAUNCHERS:
        return None  # explicit WSL invocation is fine on native Windows too

    is_unix_only = system == "Windows" and (
        program.endswith(_UNIX_SCRIPT_EXTENSION)
        or (program in _UNIX_ONLY_PROGRAMS and not shutil.which(program))
    )
    is_windows_only = system != "Windows" and (
        program.endswith(_WINDOWS_SCRIPT_EXTENSIONS)
        or program in _WINDOWS_ONLY_PROGRAMS
    )

    if is_unix_only:
        return (
            "is a Unix shell command (a .sh script, or bash/sh/zsh run "
            "directly), which does not run on native Windows. If this "
            "project is meant to run under WSL, invoke it as "
            "`wsl <command>` so meow can tell the difference -- or install "
            "Git for Windows (or another bash/sh/zsh) so the interpreter "
            "itself resolves on PATH."
        )
    if is_windows_only:
        return (
            "is a Windows-only command (a .bat/.cmd script, or "
            "cmd.exe/powershell.exe run directly), which does not run on "
            f"{system}."
        )
    return None


def _validate_os_compatibility(
    commands: list[LintCommand], system: str | None = None
) -> None:
    """Fail fast on a lint command that can only ever run on a different OS
    than the one meow is running on right now -- before any agent runs,
    rather than failing obscurely partway through a lint pass."""
    system = system or platform.system()
    for cmd in commands:
        problem = _os_mismatch(cmd.command, system)
        if problem:
            raise ValueError(
                f"{CONFIG_FILENAME}: lint command {cmd.command!r} {problem} "
                "Fix or remove this [[lint]] entry for this machine."
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
    _validate_os_compatibility(config["lint"])

    return config

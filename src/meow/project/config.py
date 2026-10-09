"""
meow/config.py

Lint-command modeling and MEOW configuration loading -- the project-specific
values every role reads through a `Sprint`, kept separate from the agent
wiring and orchestration that consume them.
"""

import os
import platform
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback -- pip install tomli

CONFIG_FILENAME = ".meow/config.toml"
LOCAL_CONFIG_FILENAME = ".meow/config.local.toml"
LEGACY_CONFIG_FILENAME = ".harness.toml"
USER_CONFIG_FILENAME = ".meow/config.toml"
DEFAULT_FIX_FLAG = "--fix"
LINT_ENTRY_KEYS = frozenset({
    "command",
    "fix_flag",
    "per_file",
    "gate",
    "cwd",
    "include",
    "exclude",
    "timeout",
    "env",
    "args",
})
TESTER_KEYS = frozenset({
    "tests",
    "dev_server",
    "mcp",
    "test_dirs",
    "base_url",
    "architecture_files",
    "browser",
})
TEST_ENTRY_KEYS = frozenset({"cwd", "command", "args", "timeout", "env", "gate"})


SERVER_ENTRY_KEYS = frozenset({
    "cwd",
    "command",
    "args",
    "env",
    "ready_url",
    "startup_timeout",
})
MCP_ENTRY_KEYS = frozenset({"name", "command", "args", "env"})
_DRIVE_PREFIX_LENGTH = 2


def split_command(command: str) -> list[str]:
    """Split a configured command consistently for launch and readiness checks."""
    argv = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        argv = [
            value[1:-1]
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'"
            else value
            for value in argv
        ]
        if argv:
            argv[0] = argv[0].replace("\\", "/")
    return argv


DEFAULT_CONFIG = {
    "lint_command": None,  # legacy single-command form
    "lint_fix_flag": DEFAULT_FIX_FLAG,
    "max_rounds": 8,
    "lint_timeout": 60,  # seconds before a per-file lint command is killed
    "docs_dir": ".meow/plans",
    "models": {
        "explorer": "haiku",
        "planner": None,  # None = engine default
        "generator": None,
        "reviewer": None,
        "issue_fetcher": None,
        "gitlab_fetcher": None,
        "lint_fixer": None,
        "review_fixer": None,
        "tester": None,
    },
    "delivery": {
        "target_branch": "dev",
        "gitlab": {"enabled": False},
    },
}

_CONFIG_ENV_VAR = re.compile(
    r"%([A-Za-z_][A-Za-z0-9_]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


def _config_environment_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value
    if name == "USERPROFILE":
        return os.environ.get("HOME") or str(Path.home())
    return None


def _expand_config_environment(value):
    """Expand common environment-variable syntaxes in TOML values."""
    if isinstance(value, str):
        return _CONFIG_ENV_VAR.sub(
            lambda match: (
                _config_environment_value(
                    match.group(1) or match.group(2) or match.group(3)
                )
                or match.group(0)
            ),
            value,
        )
    if isinstance(value, list):
        return [_expand_config_environment(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _expand_config_environment(item) for key, item in value.items()
        }
    return value


def _merge_config(base: dict, override: dict) -> dict:
    """Merge config layers, appending skill lists across every layer."""
    merged = dict(base)
    for key, value in override.items():
        if key == "agent_skills" and isinstance(merged.get(key), dict) and isinstance(value, dict):
            skills = dict(merged[key])
            for role, additions in value.items():
                previous = skills.get(role)
                if isinstance(previous, list) and isinstance(additions, list):
                    skills[role] = [*previous, *additions]
                else:
                    skills[role] = additions
            merged[key] = skills
            continue
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def user_config_path() -> Path:
    """Return the optional per-user fallback config path."""
    return Path.home() / USER_CONFIG_FILENAME


def config_paths(working_dir: Path) -> tuple[Path, ...]:
    """Return configs from highest priority to lowest priority."""
    project = Path(working_dir)
    local = project / LOCAL_CONFIG_FILENAME
    shared = project / CONFIG_FILENAME
    legacy = project / LEGACY_CONFIG_FILENAME
    paths = []
    if local.is_file():
        paths.append(local)
    primary = shared if shared.is_file() else legacy if legacy.is_file() else None
    if primary is not None:
        paths.append(primary)
    fallback = user_config_path()
    if fallback.is_file() and fallback.resolve() not in {
        path.resolve() for path in paths
    }:
        paths.append(fallback)
    return tuple(paths)


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
    cwd: Path = Path(".")
    include: tuple[Path, ...] = ()
    exclude: tuple[Path, ...] = ()
    timeout: float | None = None
    env: dict[str, str] | None = None
    args: tuple[str, ...] = ()

    def argv(self) -> list[str]:
        """The command as argv, check-only, program resolved on PATH."""
        argv = split_command(self.command) + list(self.args)
        if not argv:
            raise ValueError(f"{CONFIG_FILENAME}: lint command is empty.")
        # Windows will not exec a .cmd shim (npx, eslint and oxlint all ship
        # as one) from a bare argv, so look the program up the way a shell
        # would. Falls back to the original name so a genuinely missing
        # program still surfaces as the OS error rather than being hidden.
        resolved = shutil.which(argv[0])
        # Native Windows executables run correctly through CreateProcess by
        # their bare name. Only resolve shell shims, which cannot be launched
        # directly by asyncio without their absolute path.
        if resolved and os.name == "nt" and Path(resolved).suffix.lower() not in {
            ".bat",
            ".cmd",
        }:
            resolved = argv[0]
        return [resolved or argv[0], *argv[1:]]

    def argv_for_file(self, file_path: str) -> list[str]:
        """The command as argv for one file, auto-fixing where supported."""
        argv = self.argv()
        if self.fix_flag:
            argv.append(self.fix_flag)
        argv.append(file_path)
        return argv

    def matches_file(self, file_path: Path) -> bool:
        """Whether a repo-relative file belongs to this command's component."""
        path = Path(os.path.normpath(str(file_path).replace("\\", "/")))
        cwd = self.cwd
        try:
            path.relative_to(cwd) if cwd != Path(".") else path
        except ValueError:
            return False
        if self.include and not any(
            _is_path_prefix(path, item) for item in self.include
        ):
            return False
        return not any(_is_path_prefix(path, item) for item in self.exclude)


@dataclass(frozen=True)
class TestCommand:
    cwd: Path
    command: str
    args: tuple[str, ...] = ()
    timeout: float = 300
    env: dict[str, str] | None = None
    gate: bool = True


@dataclass(frozen=True)
class DevServerCommand:
    cwd: Path
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    ready_url: str = ""
    startup_timeout: float = 30


def _is_path_prefix(path: Path, prefix: Path) -> bool:
    try:
        path.relative_to(prefix)
        return True
    except ValueError:
        return False


def resolve_command_cwd(active_dir: Path, relative: Path) -> Path:
    """Resolve a command cwd and ensure it exists inside the active checkout."""
    active = active_dir.resolve()
    candidate = (active / relative).resolve()
    try:
        candidate.relative_to(active)
    except ValueError as exc:
        raise ValueError(
            f"command cwd {relative!s} must resolve inside {active}"
        ) from exc
    if not candidate.is_dir():
        raise ValueError(f"command cwd {relative!s} does not exist as a directory")
    return candidate


def _path_value(value: object, table: str, position: int, key: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} key {key!r} must be "
            "a relative path string"
        )
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or (len(normalized) >= _DRIVE_PREFIX_LENGTH and normalized[1] == ":")
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} key {key!r} must be "
            "a relative path"
        )
    return Path(*path.parts) if path.parts else Path(".")


def _string_list(  # ruff: ignore[too-many-arguments]
    raw: dict, key: str, table: str, position: int, *, paths: bool = False
) -> tuple:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} {key!r} must be "
            "a list of non-empty strings"
        )
    if paths:
        return tuple(_path_value(item, table, position, key) for item in value)
    return tuple(value)


def _env_value(raw: dict, table: str, position: int) -> dict[str, str]:
    value = raw.get("env", {})
    if not isinstance(value, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} 'env' must map "
            "strings to strings"
        )
    return dict(value)


def _positive_timeout(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    raw: dict, key: str, default: float, table: str, position: int
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} {key!r} must be "
            "a positive number"
        )
    return float(value)


def _typed_bool(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    raw: dict, key: str, default: bool, table: str, position: int
) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} {key!r} must be a boolean"
        )
    return value


def _command_fields(
    raw: object, table: str, position: int, allowed: frozenset
) -> tuple:
    if not isinstance(raw, dict):
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} must be a TOML table"
        )
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} has unknown key(s) {unknown}"
        )
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(
            f"{CONFIG_FILENAME}: {table} entry {position} must set a "
            "non-empty 'command'"
        )
    cwd = _path_value(raw.get("cwd", "."), table, position, "cwd")
    args = _string_list(raw, "args", table, position)
    env = _env_value(raw, table, position)
    return cwd, command, args, env


def _normalize_tester_config(user_config: dict) -> dict:  # ruff: ignore[too-many-locals]
    raw = user_config.get("tester", {})
    if not isinstance(raw, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [tester] must be a table")
    unknown = sorted(set(raw) - TESTER_KEYS)
    if unknown:
        raise ValueError(f"{CONFIG_FILENAME}: [tester] has unknown key(s) {unknown}")
    tests = []
    raw_tests = raw.get("tests", [])
    if not isinstance(raw_tests, list):
        raise ValueError(
            f"{CONFIG_FILENAME}: [[tester.tests]] must be an array of tables"
        )
    for position, entry in enumerate(raw_tests, 1):
        cwd, command, args, env = _command_fields(
            entry, "[[tester.tests]]", position, TEST_ENTRY_KEYS
        )
        tests.append(
            TestCommand(
                cwd,
                command,
                args,
                _positive_timeout(entry, "timeout", 300, "[[tester.tests]]", position),
                env,
                _typed_bool(entry, "gate", True, "[[tester.tests]]", position),
            )
        )
    servers = []
    raw_servers = raw.get("dev_server", [])
    if not isinstance(raw_servers, list):
        raise ValueError(
            f"{CONFIG_FILENAME}: [[tester.dev_server]] must be an array of tables"
        )
    for position, entry in enumerate(raw_servers, 1):
        cwd, command, args, env = _command_fields(
            entry, "[[tester.dev_server]]", position, SERVER_ENTRY_KEYS
        )
        ready_url = entry.get("ready_url")
        if not isinstance(ready_url, str) or not ready_url:
            raise ValueError(
                f"{CONFIG_FILENAME}: [[tester.dev_server]] entry {position} "
                "must set 'ready_url'"
            )
        servers.append(
            DevServerCommand(
                cwd,
                command,
                args,
                env,
                ready_url,
                _positive_timeout(
                    entry, "startup_timeout", 30, "[[tester.dev_server]]", position
                ),
            )
        )
    mcp = []
    names = set()
    raw_mcp = raw.get("mcp", [])
    if not isinstance(raw_mcp, list):
        raise ValueError(
            f"{CONFIG_FILENAME}: [[tester.mcp]] must be an array of tables"
        )
    for position, entry in enumerate(raw_mcp, 1):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{CONFIG_FILENAME}: [[tester.mcp]] entry {position} "
                "must be a TOML table"
            )
        unknown = sorted(set(entry) - MCP_ENTRY_KEYS)
        if unknown:
            raise ValueError(
                f"{CONFIG_FILENAME}: [[tester.mcp]] entry {position} has "
                f"unknown key(s) {unknown}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(
                f"{CONFIG_FILENAME}: [[tester.mcp]] entry {position} "
                "requires a unique non-empty name"
            )
        names.add(name)
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(
                f"{CONFIG_FILENAME}: [[tester.mcp]] entry {position} must "
                "set a non-empty 'command'"
            )
        mcp.append({
            "name": name,
            "command": command,
            "args": list(_string_list(entry, "args", "[[tester.mcp]]", position)),
            "env": _env_value(entry, "[[tester.mcp]]", position),
        })
    test_dirs = _string_list(raw, "test_dirs", "[tester]", 1, paths=True)
    architecture = _string_list(raw, "architecture_files", "[tester]", 1, paths=True)
    base_url = raw.get("base_url")
    if base_url is not None and (not isinstance(base_url, str) or not base_url):
        raise ValueError(
            f"{CONFIG_FILENAME}: [tester] 'base_url' must be a non-empty string"
        )
    browser = raw.get("browser")
    if browser is not None:
        if not isinstance(browser, dict):
            raise ValueError(f"{CONFIG_FILENAME}: [tester.browser] must be a table")
        allowed = {
            "kind",
            "name",
            "entrypoint",
            "inputs",
            "outputs",
            "permissions",
            "required",
            "args",
            "cwd",
            "timeout",
            "env",
            "flows",
            "artifacts",
        }
        unknown = sorted(set(browser) - allowed)
        if unknown:
            raise ValueError(
                f"{CONFIG_FILENAME}: [tester.browser] has unknown key(s) {unknown}"
            )
        if browser.get("kind") not in {"skill", "mcp", "command"}:
            raise ValueError(
                f"{CONFIG_FILENAME}: [tester.browser] kind must be 'skill', 'mcp', or 'command'"
            )
        for key in ("name", "entrypoint"):
            if not isinstance(browser.get(key), str) or not browser[key].strip():
                raise ValueError(
                    f"{CONFIG_FILENAME}: [tester.browser] requires {key!r}"
                )
        for key in ("inputs", "outputs", "permissions"):
            if key in browser and not isinstance(browser[key], (dict, list)):
                raise ValueError(
                    f"{CONFIG_FILENAME}: [tester.browser] {key!r} must be a table or list"
                )
        browser = dict(browser)
        browser["required"] = _typed_bool(
            browser, "required", False, "[tester.browser]", 1
        )
        if browser["kind"] == "command":
            browser["args"] = list(_string_list(browser, "args", "[tester.browser]", 1))
            browser["flows"] = list(
                _string_list(browser, "flows", "[tester.browser]", 1)
            )
            browser["artifacts"] = [
                str(path)
                for path in _string_list(
                    browser, "artifacts", "[tester.browser]", 1, paths=True
                )
            ]
            browser["cwd"] = _path_value(
                browser.get("cwd", "."), "[tester.browser]", 1, "cwd"
            )
            browser["timeout"] = _positive_timeout(
                browser, "timeout", 300, "[tester.browser]", 1
            )
            browser["env"] = _env_value(browser, "[tester.browser]", 1)
    return {
        "tests": tests,
        "dev_server": servers,
        "mcp": mcp,
        "test_dirs": test_dirs,
        "base_url": base_url,
        "architecture_files": architecture,
        "browser": browser,
    }


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
    if (
        "fix_flag" in raw
        and raw["fix_flag"] is not None
        and (not isinstance(raw["fix_flag"], str) or not raw["fix_flag"])
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: [[lint]] entry {position} 'fix_flag' must "
            "be a string or null"
        )
    cwd = _path_value(raw.get("cwd", "."), "[[lint]]", position, "cwd")
    return LintCommand(
        command=raw["command"],
        fix_flag=raw.get("fix_flag"),
        per_file=_typed_bool(raw, "per_file", True, "[[lint]]", position),
        gate=_typed_bool(raw, "gate", True, "[[lint]]", position),
        cwd=cwd,
        include=_string_list(raw, "include", "[[lint]]", position, paths=True),
        exclude=_string_list(raw, "exclude", "[[lint]]", position, paths=True),
        timeout=_positive_timeout(raw, "timeout", 60, "[[lint]]", position)
        if "timeout" in raw
        else None,
        env=_env_value(raw, "[[lint]]", position),
        args=_string_list(raw, "args", "[[lint]]", position),
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
        problem = _os_mismatch(" ".join([cmd.command, *cmd.args]), system)
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
        _lint_entry(raw, position) for position, raw in enumerate(raw_entries, start=1)
    )

    if not entries:
        raise ValueError(
            f"{CONFIG_FILENAME} must define at least one lint command -- "
            "either a [[lint]] entry with a 'command' key, or the "
            'single-command form lint_command = "ruff check".'
        )

    return entries


def _validate_max_rounds(config: dict) -> None:
    """Reject a non-positive or non-integer `max_rounds` up front.

    `max_rounds <= 0` doesn't crash on its own -- every round-loop shape
    computes an empty `range()` and falls straight through to "didn't pass"
    -- but that means a real planner call (and, depending on the loop
    shape, a wasted generator/fixer session start too) runs first, only to
    report a confusing "did not pass after 0 rounds" with no round ever
    actually attempted. Caught here instead, before any agent runs at all.
    """
    max_rounds = config["max_rounds"]
    is_valid = (
        isinstance(max_rounds, int)
        and not isinstance(max_rounds, bool)
        and max_rounds >= 1
    )
    if not is_valid:
        raise ValueError(
            f"{CONFIG_FILENAME}: max_rounds must be a positive integer, got "
            f"{max_rounds!r}."
        )


def load_config(working_dir: Path) -> dict:
    from meow.infrastructure.checks import normalize_build
    from meow.infrastructure.worktree_setup import validate_setup
    from meow.project.permissions import parse_policy

    user_config = {}
    for config_path in reversed(config_paths(working_dir)):
        with open(config_path, "rb") as f:
            user_config = _merge_config(
                user_config, _expand_config_environment(tomllib.load(f))
            )

    config = {**DEFAULT_CONFIG, **user_config}
    config["models"] = {**DEFAULT_CONFIG["models"], **user_config.get("models", {})}
    lint_configured = any(
        key in user_config for key in ("lint", "lint_command", "lint_fix_flag")
    )
    config["lint"] = _normalize_lint_commands(user_config) if lint_configured else []
    _validate_os_compatibility(config["lint"])
    config["tester"] = _normalize_tester_config(user_config)
    config["build"] = normalize_build(user_config.get("build", []))
    config["permissions"] = parse_policy(user_config.get("permissions"))
    config["worktree_setup"] = validate_setup(user_config.get("worktree_setup"))
    delivery = user_config.get("delivery", {})
    if not isinstance(delivery, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [delivery] must be a table")
    target = delivery.get("target_branch", "dev")
    if not isinstance(target, str) or not target or target.startswith("-"):
        raise ValueError(
            f"{CONFIG_FILENAME}: [delivery].target_branch must be a branch name"
        )
    gitlab = delivery.get("gitlab", {})
    if not isinstance(gitlab, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [delivery.gitlab] must be a table")
    config["delivery"] = {
        "target_branch": target,
        "gitlab": {"enabled": bool(gitlab.get("enabled", False))},
    }
    for table_name, commands in (
        ("[[tester.tests]]", config["tester"]["tests"]),
        ("[[tester.dev_server]]", config["tester"]["dev_server"]),
    ):
        for position, command in enumerate(commands, 1):
            problem = _os_mismatch(
                " ".join([command.command, *command.args]), platform.system()
            )
            if problem:
                raise ValueError(
                    f"{CONFIG_FILENAME}: {table_name} entry {position} "
                    f"command {command.command!r} {problem}"
                )
    _validate_max_rounds(config)

    return config

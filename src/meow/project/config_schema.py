"""Schema validation that turns raw config tables into typed command models."""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from meow.project.config_files import CONFIG_FILENAME
from meow.project.config_models import (
    DevServerCommand,
    LintCommand,
    VerificationCommand,
)

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


BROWSER_CHANNELS = frozenset({
    "chrome",
    "chrome-beta",
    "chrome-dev",
    "chrome-canary",
    "msedge",
    "msedge-beta",
    "msedge-dev",
})
_BROWSER_SELECTION_KEYS = (
    "browser_channel",
    "browser_executable",
    "browser_playwright",
)
_PLAYWRIGHT_ENV_KEY = re.compile(r"PLAYWRIGHT_[A-Z0-9_]+")
_PORT_PLACEHOLDER_URL = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^/{}?#]*:\{port\}(?:[/?#][^{}]*)?"
)

TESTER_KEYS = frozenset({
    "browser_channel",
    "browser_executable",
    "browser_playwright",
    "browser_env",
    "auto_test",
    "tests",
    "dev_server",
    "mcp",
    "test_dirs",
    "base_url",
    "architecture_files",
    "browser",
    "write_tests",
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


def _browser_settings(raw: dict) -> dict:
    """Validate the explicit browser selection and the Playwright environment."""
    channel = raw.get("browser_channel")
    if channel is not None and channel not in BROWSER_CHANNELS:
        raise ValueError(
            f"{CONFIG_FILENAME}: [tester] 'browser_channel' must be one of "
            f"{sorted(BROWSER_CHANNELS)}; for example:\n"
            '[tester]\nbrowser_channel = "chrome"'
        )
    executable = raw.get("browser_executable")
    if executable is not None and (
        not isinstance(executable, str)
        or not executable
        or not (
            PurePosixPath(executable).is_absolute()
            or PureWindowsPath(executable).is_absolute()
        )
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: [tester] 'browser_executable' must be an absolute "
            "path; for example:\n[tester]\n"
            'browser_executable = "C:\\\\Program Files\\\\Google\\\\Chrome\\\\'
            'Application\\\\chrome.exe"'
        )
    playwright = raw.get("browser_playwright", False)
    if not isinstance(playwright, bool):
        raise ValueError(
            f"{CONFIG_FILENAME}: [tester] 'browser_playwright' must be a boolean"
        )
    selected = [
        key
        for key, value in (
            ("browser_channel", channel),
            ("browser_executable", executable),
            ("browser_playwright", playwright or None),
        )
        if value is not None
    ]
    if len(selected) > 1:
        raise ValueError(
            f"{CONFIG_FILENAME}: [tester] sets more than one browser selection "
            f"({', '.join(selected)}); keep exactly one of {list(_BROWSER_SELECTION_KEYS)}"
        )
    env = raw.get("browser_env", {})
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: [tester] 'browser_env' must map strings to strings"
        )
    for key in env:
        if not _PLAYWRIGHT_ENV_KEY.fullmatch(key):
            raise ValueError(
                f"{CONFIG_FILENAME}: [tester] 'browser_env' key {key!r} must match "
                "PLAYWRIGHT_*"
            )
    return {
        "browser_channel": channel,
        "browser_executable": executable,
        "browser_playwright": playwright,
        "browser_env": dict(env),
    }


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
            VerificationCommand(
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
        if (
            "{" in ready_url or "}" in ready_url
        ) and not _PORT_PLACEHOLDER_URL.fullmatch(ready_url):
            raise ValueError(
                f"{CONFIG_FILENAME}: [[tester.dev_server]] entry {position} "
                "'ready_url' may contain {port} only as the port"
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
    write_tests = raw.get("write_tests", False)
    if not isinstance(write_tests, bool):
        raise ValueError(f"{CONFIG_FILENAME}: [tester] 'write_tests' must be a boolean")
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
        "auto_test": _typed_bool(raw, "auto_test", bool(tests), "[tester]", 1),
        "dev_server": servers,
        "mcp": mcp,
        "test_dirs": test_dirs,
        "base_url": base_url,
        "architecture_files": architecture,
        "browser": browser,
        "write_tests": write_tests,
        **_browser_settings(raw),
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


def _normalize_lint_commands(user_config: dict) -> list[LintCommand]:
    """Validate and normalize the configured lint commands.

    Any number of commands is allowed. The TOML array of tables gives each
    command its own fix flag and role:

        [[lint]]
        command = "npx oxlint"
        fix_flag = "--fix"

        [[lint]]
        command = "npx fallow"
        per_file = false          # project-wide only, never per file
        gate = false              # non-blocking: failure is not a sprint FAIL

    Every project must use the ``[[lint]]`` form. There is no implicit
    single-command configuration.
    """
    unsupported = {"lint_command", "lint_fix_flag"} & user_config.keys()
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(
            f"{CONFIG_FILENAME}: unsupported lint configuration key(s): {names}. "
            "Use [[lint]] entries instead."
        )

    raw_entries = user_config.get("lint", [])
    if not isinstance(raw_entries, list):
        raise ValueError(
            f"{CONFIG_FILENAME}: 'lint' must be a list of [[lint]] tables."
        )
    entries = [
        _lint_entry(raw, position) for position, raw in enumerate(raw_entries, start=1)
    ]

    if not entries:
        raise ValueError(
            f"{CONFIG_FILENAME} must define at least one lint command -- "
            "add a [[lint]] entry with a 'command' key."
        )

    return entries


ORCHESTRATOR_DEFAULTS = {
    "max_parallel": 3,
    "max_recovery_cycles": 3,
    "review_rounds": 3,
    "branch_prefix": "orchestrator/",
    "poll_seconds": 5,
    "open_pr": True,
}


def _orchestrator_int(table: dict, key: str, minimum: int) -> int:
    value = table.get(key, ORCHESTRATOR_DEFAULTS[key])
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(
            f"{CONFIG_FILENAME}: [orchestrator].{key} must be an integer "
            f">= {minimum}, got {value!r}."
        )
    return value


def _orchestrator_prefix(table: dict) -> str:
    prefix = table.get("branch_prefix", ORCHESTRATOR_DEFAULTS["branch_prefix"])
    if (
        not isinstance(prefix, str)
        or not prefix
        or any(char.isspace() for char in prefix)
        or ".." in prefix
        or prefix.startswith("-")
    ):
        raise ValueError(
            f"{CONFIG_FILENAME}: [orchestrator].branch_prefix must be a "
            "non-empty string without spaces or '..' that does not start with "
            f"'-', got {prefix!r}."
        )
    return prefix


def _normalize_orchestrator_config(user_config: dict) -> dict:
    """Validate `[orchestrator]` and fill in the pinned defaults."""
    table = user_config.get("orchestrator", {})
    if not isinstance(table, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [orchestrator] must be a table")
    unknown = sorted(set(table) - set(ORCHESTRATOR_DEFAULTS))
    if unknown:
        raise ValueError(
            f"{CONFIG_FILENAME}: [orchestrator] has unknown keys: {', '.join(unknown)}"
        )
    poll = table.get("poll_seconds", ORCHESTRATOR_DEFAULTS["poll_seconds"])
    if isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll <= 0:
        raise ValueError(
            f"{CONFIG_FILENAME}: [orchestrator].poll_seconds must be a number "
            f"> 0, got {poll!r}."
        )
    open_pr = table.get("open_pr", ORCHESTRATOR_DEFAULTS["open_pr"])
    if not isinstance(open_pr, bool):
        raise ValueError(
            f"{CONFIG_FILENAME}: [orchestrator].open_pr must be true or false, "
            f"got {open_pr!r}."
        )
    return {
        "max_parallel": _orchestrator_int(table, "max_parallel", 1),
        "max_recovery_cycles": _orchestrator_int(table, "max_recovery_cycles", 0),
        "review_rounds": _orchestrator_int(table, "review_rounds", 1),
        "branch_prefix": _orchestrator_prefix(table),
        "poll_seconds": poll,
        "open_pr": open_pr,
    }


PIPELINE_DEFAULTS = {
    "open_mr": True,
    "max_fix_rounds": 3,
    "start_timeout": 300,
    "timeout": 3600,
    "poll_seconds": 15,
    "fix_non_meow_jobs": False,
}


def _pipeline_value(table: dict, key: str):
    default = PIPELINE_DEFAULTS[key]
    value = table.get(key, default)
    if isinstance(default, bool):
        valid, need = isinstance(value, bool), "true or false"
    elif key == "max_fix_rounds":
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        need = "an integer >= 0"
    else:
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )
        need = "a number > 0"
    if not valid:
        raise ValueError(
            f"{CONFIG_FILENAME}: [pipeline].{key} must be {need}, got {value!r}."
        )
    return value


def _normalize_pipeline_config(user_config: dict) -> dict:
    """Validate `[pipeline]` and fill in the pinned defaults."""
    table = user_config.get("pipeline", {})
    if not isinstance(table, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [pipeline] must be a table")
    unknown = sorted(set(table) - set(PIPELINE_DEFAULTS))
    if unknown:
        raise ValueError(
            f"{CONFIG_FILENAME}: [pipeline] has unknown keys: {', '.join(unknown)}"
        )
    return {key: _pipeline_value(table, key) for key in PIPELINE_DEFAULTS}


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


MIN_STALL_ROUNDS = 2


def _validate_stall_rounds(config: dict) -> None:
    """Reject a `stall_rounds` that is neither 0 (off) nor an integer >= 2.

    One identical failed round is not yet a pattern, so 1 would stop every
    loop at its first failure.
    """
    stall_rounds = config["stall_rounds"]
    is_valid = (
        isinstance(stall_rounds, int)
        and not isinstance(stall_rounds, bool)
        and (stall_rounds == 0 or stall_rounds >= MIN_STALL_ROUNDS)
    )
    if not is_valid:
        raise ValueError(
            f"{CONFIG_FILENAME}: stall_rounds must be 0 (off) or an integer "
            f"of at least 2, got {stall_rounds!r}."
        )


def _validate_max_parallel_tasks(config: dict) -> None:
    """Reject a non-positive or non-integer `max_parallel_tasks` up front."""
    value = config["max_parallel_tasks"]
    if not (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
        raise ValueError(
            f"{CONFIG_FILENAME}: max_parallel_tasks must be a positive integer, "
            f"got {value!r}."
        )


# `[run]`: the bounded retry ladder for parked runs (seconds where timed).
# auto_retries: supervisor relaunches per park without progress; retry_backoff:
# waits between them; managed_resumes: resumes a managing session may run per
# parked run, and attempts per queue item before requeueing stops;
# max_total_resumes: cumulative cap; api_max_wait: longest API wait before
# parking; worker_startup_grace: time a detached worker gets to register;
# supervise: run foreground `meow run` under the supervisor (as under Claude
# Code); background runs are always supervised; stale_after: silence after
# which a native `intent=run` record counts as a lost session and is parked.
RUN_DEFAULTS = {
    "auto_retries": 3,
    "retry_backoff": [60, 300, 900],
    "managed_resumes": 6,
    "max_total_resumes": 20,
    "api_max_wait": 10800,
    "worker_startup_grace": 180,
    "supervise": False,
    "stale_after": 7200,
}


def _run_value(table: dict, key: str):
    default = RUN_DEFAULTS[key]
    value = table.get(key, default)
    if isinstance(default, bool):
        valid, need = isinstance(value, bool), "true or false"
    elif key == "retry_backoff":
        valid = (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and item >= 0
                for item in value
            )
        )
        need = "a non-empty list of numbers >= 0"
    elif key in {"api_max_wait", "worker_startup_grace", "stale_after"}:
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )
        need = "a number > 0"
    else:
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        need = "an integer >= 0"
    if not valid:
        raise ValueError(
            f"{CONFIG_FILENAME}: [run].{key} must be {need}, got {value!r}."
        )
    return list(value) if isinstance(value, list) else value


def _normalize_run_config(user_config: dict) -> dict:
    """Validate `[run]` and fill in the retry-ladder defaults."""
    table = user_config.get("run", {})
    if not isinstance(table, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [run] must be a table")
    unknown = sorted(set(table) - set(RUN_DEFAULTS))
    if unknown:
        raise ValueError(
            f"{CONFIG_FILENAME}: [run] has unknown keys: {', '.join(unknown)}"
        )
    return {key: _run_value(table, key) for key in RUN_DEFAULTS}

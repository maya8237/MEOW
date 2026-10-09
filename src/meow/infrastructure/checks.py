"""Current, independently recorded lint/test/build gate evidence."""

import asyncio
import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from meow.execution.run_state import MAX_OUTPUT, CheckResult
from meow.infrastructure.test_runner import prepared_test_stage
from meow.project.config import (
    LintCommand,
    VerificationCommand,
    config_paths,
    resolve_command_cwd,
    split_command,
)
from meow.project.config_schema import _command_fields, _positive_timeout, _typed_bool


@dataclass(frozen=True)
class Check:
    kind: str
    name: str
    command: str
    args: tuple[str, ...] = ()
    required: bool = True
    timeout: float = 300
    cwd: Path = Path(".")
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    command: str
    cwd: Path | None
    output: str = ""
    changed_files: tuple[str, ...] = ()


def _preflight_snapshot(repo: Path) -> dict[str, str]:
    root = repo.resolve()
    result = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in {".git", ".meow", ".venv"} for part in relative.parts):
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def preflight_check(  # ruff: ignore[complex-structure, too-many-statements]
    repo: Path, check: Check, *, execute: bool = False
) -> PreflightResult:
    """Validate a proposed check, optionally running it after user review.

    The default is read-only. An executed preflight reports any file changes so
    onboarding can ask the user before accepting the command.
    """
    try:
        cwd = resolve_command_cwd(repo, check.cwd)
    except (OSError, ValueError) as exc:
        return PreflightResult("invalid_cwd", check.command, None, str(exc))
    argv = [*split_command(check.command), *check.args]
    if not argv or not (
        shutil.which(argv[0]) or (cwd / argv[0]).is_file()
    ):
        return PreflightResult("missing_executable", check.command, cwd)
    if not execute:
        return PreflightResult("ready_unchecked", check.command, cwd)
    before = _preflight_snapshot(repo)
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env={**os.environ, **(check.env or {})},
            capture_output=True,
            timeout=check.timeout,
            check=False,
        )
        output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        status = "passed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        output = str(exc)
        status = "timed_out"
    except OSError as exc:
        output = str(exc)
        status = "failed"
    after = _preflight_snapshot(repo)
    changed = tuple(
        sorted(
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        )
    )
    if changed:
        status = "changed_files"
    return PreflightResult(status, check.command, cwd, output[:MAX_OUTPUT], changed)


def check_identity(check: Check) -> str:
    payload = repr((
        check.kind,
        check.name,
        check.command,
        check.args,
        check.required,
        check.timeout,
        str(check.cwd),
        check.env,
    ))
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize_build(raw: object) -> list[Check]:
    if not isinstance(raw, list):
        raise ValueError(".meow/config.toml: [[build]] must be an array of tables")
    entries = []
    for position, item in enumerate(raw, 1):
        cwd, command, args, env = _command_fields(
            item,
            "[[build]]",
            position,
            frozenset({"command", "args", "cwd", "env", "timeout", "required"}),
        )
        entries.append(
            Check(
                "build",
                f"build-{position}",
                command,
                args,
                _typed_bool(item, "required", True, "[[build]]", position),
                _positive_timeout(item, "timeout", 300, "[[build]]", position),
                cwd,
                env,
            )
        )
    return entries


def config_fingerprint(repo: Path) -> str:
    paths = config_paths(repo)
    if not paths:
        raise FileNotFoundError("No MEOW configuration file found")
    digest = hashlib.sha256()
    for path in paths:
        try:
            label = str(path.relative_to(repo))
        except ValueError:
            label = str(path.resolve())
        digest.update(label.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def code_revision(repo: Path) -> str:
    """Hash HEAD and content, including edits that have not been committed."""
    root = repo.resolve()
    digest = hashlib.sha256()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=False
    )
    digest.update(head.stdout)
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if files.returncode:
        paths = [
            p
            for p in root.rglob("*")
            if p.is_file()
            and not any(
                part in {".git", ".venv", ".meow", ".test-tmp"} for part in p.parts
            )
        ]
    else:
        paths = [root / os.fsdecode(name) for name in files.stdout.split(b"\0") if name]
    for path in sorted(paths):
        if path.is_file() and not any(
            part in {".meow", ".test-tmp"} for part in path.relative_to(root).parts
        ):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def configured_checks(config: dict) -> list[Check]:
    checks = []
    for index, entry in enumerate(config["lint"], 1):
        assert isinstance(entry, LintCommand)
        checks.append(
            Check(
                "lint",
                f"lint-{index}",
                entry.command,
                entry.args,
                entry.gate,
                entry.timeout or config["lint_timeout"],
                entry.cwd,
                entry.env,
            )
        )
    for index, entry in enumerate(config["tester"]["tests"], 1):
        assert isinstance(entry, VerificationCommand)
        checks.append(
            Check(
                "test",
                f"test-{index}",
                entry.command,
                entry.args,
                entry.gate,
                entry.timeout,
                entry.cwd,
                entry.env,
            )
        )
    checks.extend(config.get("build", []))
    browser = config.get("tester", {}).get("browser")
    if isinstance(browser, dict):
        checks.append(
            Check(
                "browser",
                str(browser.get("name", "browser")),
                str(browser.get("entrypoint", "unavailable")),
                tuple(browser.get("args", [])),
                bool(browser.get("required", False)),
                float(browser.get("timeout", 300)),
                Path(browser.get("cwd", ".")),
                browser.get("env"),
            )
        )
    return checks


def run_check(repo: Path, check: Check) -> CheckResult:
    """Execute lint or build. Tests use the existing prepared_test_stage API."""
    if check.kind == "test":
        raise ValueError("Test checks run through prepared_test_stage")
    before = time.monotonic()
    revision = code_revision(repo)
    fingerprint = config_fingerprint(repo)
    argv = [*split_command(check.command), *check.args]
    try:
        result = subprocess.run(
            argv,
            cwd=resolve_command_cwd(repo, check.cwd),
            env={**os.environ, **(check.env or {})},
            capture_output=True,
            timeout=check.timeout,
            check=False,
        )
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        exit_code, timed_out = result.returncode, False
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or b"") + (exc.stderr or b"")).decode(
            "utf-8", errors="replace"
        )
        exit_code, timed_out = None, True
    except OSError as exc:
        output, exit_code, timed_out = str(exc), None, False
    return CheckResult(
        check.kind,
        check.command,
        check.required,
        exit_code,
        time.monotonic() - before,
        output[:MAX_OUTPUT],
        revision,
        fingerprint,
        timed_out,
        check_identity(check),
    )


def checks_current(results: list[CheckResult], checks: list[Check], repo: Path) -> bool:
    revision = code_revision(repo)
    fingerprint = config_fingerprint(repo)
    for check in checks:
        matches = [r for r in results if r.identity == check_identity(check)]
        if not matches:
            return False
        result = matches[-1]
        if check.required and (
            not result.passed
            or result.revision != revision
            or result.config_fingerprint != fingerprint
        ):
            return False
    return True


def completion_ready(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    results: list[CheckResult],
    checks: list[Check],
    repo: Path,
    reviewer: str,
    tester: str | None = None,
    *,
    reviewer_revision: str | None = None,
) -> bool:
    return (
        reviewer == "PASS"
        and tester in {None, "PASS"}
        and (reviewer_revision is None or reviewer_revision == code_revision(repo))
        and checks_current(results, checks, repo)
    )


async def run_final_checks(repo: Path, config: dict) -> list[CheckResult]:
    """Run current gates after the last edit, reusing the managed test stage."""
    checks = configured_checks(config)
    for _attempt in range(3):
        results = await _run_check_batch(repo, config, checks)
        revision = code_revision(repo)
        fingerprint = config_fingerprint(repo)
        if all(
            result.revision == revision and result.config_fingerprint == fingerprint
            for result in results
        ):
            return results
    return results


async def _run_check_batch(  # ruff: ignore[complex-structure, too-many-branches]
    repo: Path, config: dict, checks: list[Check]
) -> list[CheckResult]:
    results = []
    for check in checks:
        if check.kind not in {"test", "browser"}:
            results.append(await asyncio.to_thread(run_check, repo, check))
    tests = [check for check in checks if check.kind == "test"]
    browser_checks = [check for check in checks if check.kind == "browser"]
    if tests or browser_checks:
        revision = code_revision(repo)
        fingerprint = config_fingerprint(repo)
        start = time.monotonic()
        async with prepared_test_stage(repo, config) as evidence:
            duration = time.monotonic() - start
            for check, command in zip(tests, evidence.commands, strict=True):
                results.append(
                    CheckResult(
                        "test",
                        check.command,
                        check.required,
                        command.exit_code,
                        duration,
                        command.output[:MAX_OUTPUT],
                        revision,
                        fingerprint,
                        command.timed_out,
                        check_identity(check),
                    )
                )
            for check, browser in zip(browser_checks, evidence.browser, strict=True):
                output = browser.output
                if browser.reason:
                    output += f"\nReason: {browser.reason}"
                if browser.flows:
                    output += "\nFlows: " + ", ".join(browser.flows)
                if browser.artifacts:
                    output += "\nArtifacts: " + ", ".join(browser.artifacts)
                results.append(
                    CheckResult(
                        "browser",
                        check.command,
                        check.required,
                        0 if browser.status == "passed" else browser.exit_code,
                        duration,
                        output[:MAX_OUTPUT],
                        revision,
                        fingerprint,
                        browser.reason == "timed out",
                        check_identity(check),
                    )
                )
    return results

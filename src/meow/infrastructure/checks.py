"""Current, independently recorded lint/test/build gate evidence."""

import asyncio
import hashlib
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from meow.execution.run_state import MAX_OUTPUT, CheckResult, RunStore
from meow.infrastructure.test_runner import capture_windows_command, prepared_test_stage
from meow.project.command_policy import split_command
from meow.project.config import config_paths
from meow.project.config_files import SHARED_PROJECT_PATHS
from meow.project.config_models import (
    LintCommand,
    VerificationCommand,
    resolve_command_cwd,
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


# Tool caches that appear whenever code runs; never part of the reviewed code.
_GENERATED_DIRS = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
})


def _reviewed_path(path: Path) -> bool:
    if any(part in {".test-tmp"} | _GENERATED_DIRS for part in path.parts):
        return False
    return ".meow" not in path.parts or any(
        path.is_relative_to(shared) for shared in SHARED_PROJECT_PATHS
    )


def _code_paths(root: Path) -> tuple[Path, ...]:
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
            and not any(part in {".git", ".venv", ".test-tmp"} for part in p.parts)
        ]
    else:
        paths = [root / os.fsdecode(name) for name in files.stdout.split(b"\0") if name]
    return tuple(
        sorted(path for path in paths if _reviewed_path(path.relative_to(root)))
    )


def code_revision(repo: Path, *, paths: tuple[Path, ...] | None = None) -> str:
    """Hash HEAD and content, including edits that have not been committed."""
    root = repo.resolve()
    digest = hashlib.sha256()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=False
    )
    digest.update(head.stdout)
    for path in _code_paths(root) if paths is None else paths:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def tracked_revision(repo: Path) -> str:
    """Hash HEAD and the content of tracked files only; untracked files (reports,
    screenshots, build output) are not part of it."""
    root = repo.resolve()

    def git(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *argv], cwd=root, capture_output=True, check=False
        )

    digest = hashlib.sha256()
    head = git("rev-parse", "--verify", "-q", "HEAD")
    digest.update(head.stdout)
    # With no commit yet, the staged tree stands in for HEAD.
    diffs = (
        [git("diff", "HEAD", "--binary")]
        if head.returncode == 0
        else [
            git("diff", "--cached", "--binary"),
            git("diff", "--binary"),
        ]
    )
    if any(done.returncode for done in diffs):
        return code_revision(repo)  # not a usable repository: be strict
    for done in diffs:
        digest.update(done.stdout)
    return digest.hexdigest()


@dataclass(frozen=True)
class StageBaseline:
    """The tree a stage started from, for `adopt_stage_artifacts`."""

    revision: str
    tracked: str
    paths: tuple[Path, ...]


def stage_baseline(repo: Path) -> StageBaseline:
    paths = _code_paths(repo.resolve())
    return StageBaseline(
        code_revision(repo, paths=paths), tracked_revision(repo), paths
    )


def _stage_source_unchanged(repo: Path, baseline: StageBaseline) -> bool:
    return (
        tracked_revision(repo) == baseline.tracked
        and code_revision(repo, paths=baseline.paths) == baseline.revision
    )


def adopt_stage_artifacts(
    store: RunStore, run_id: str, repo: Path, baseline: StageBaseline
) -> None:
    """Keep a current review valid across a stage that only added files.

    Tests, builds and testers leave reports and screenshots behind. When the
    review covered the tree the stage started from and no existing file changed,
    the reviewed code is untouched, so the review is re-stamped to the new tree.
    An edit or deletion of existing source, including untracked code, invalidates it.
    """
    if store.load(run_id).results.get("reviewer_revision") != baseline.revision:
        return
    if not _stage_source_unchanged(repo, baseline):
        return
    store.update_results(run_id, reviewer_revision=code_revision(repo))


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


def kill_process_tree(process: subprocess.Popen) -> None:
    """Kill the command and everything it spawned, not just the direct child."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(OSError):
        process.kill()


def _communicate(
    process: subprocess.Popen, timeout: float, cancelled: threading.Event | None
):
    if cancelled is None:
        return process.communicate(timeout=timeout)
    deadline = time.monotonic() + timeout
    while not cancelled.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.communicate(timeout=min(remaining, 0.1))
        except subprocess.TimeoutExpired:
            continue
    raise InterruptedError("Verification cancelled")


def run_captured(  # ruff: ignore[too-many-arguments] -- cancellation belongs to the owned subprocess
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[int | None, str, bool]:
    """Run to completion; (exit code, output, timed out). A timeout kills the
    whole process tree so nothing keeps editing the checkout afterwards."""
    shell = os.name == "nt" and argv[:2] == ["cmd", "/c"]
    with subprocess.Popen(
        argv[2] if shell else argv,
        shell=shell,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    ) as process:
        try:
            data, _ = _communicate(process, timeout, cancel_event)
            return process.returncode, data.decode("utf-8", errors="replace"), False
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            try:
                data, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                data = b""
            return None, data.decode("utf-8", errors="replace"), True
        except BaseException:
            kill_process_tree(process)
            raise


async def _run_cancellable(callback):
    """Do not return from cancellation while the command's worker still runs."""
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(callback, cancel_event=cancelled))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        with suppress(Exception):
            await asyncio.shield(task)
        raise


async def run_captured_async(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float
) -> tuple[int | None, str, bool]:
    if os.name == "nt":
        return await capture_windows_command(argv, cwd, env, timeout)
    return await _run_cancellable(partial(run_captured, argv, cwd, env, timeout))


async def _run_check(
    repo: Path,
    check: Check,
    *,
    revision: str | None = None,
    fingerprint: str | None = None,
) -> CheckResult:
    """Execute lint or build. Tests use the existing prepared_test_stage API.

    A batch passes the `revision`/`fingerprint` it already computed, since
    hashing the whole tree once per check is the expensive part.
    """
    if check.kind == "test":
        raise ValueError("Test checks run through prepared_test_stage")
    before = time.monotonic()
    revision = revision or code_revision(repo)
    fingerprint = fingerprint or config_fingerprint(repo)
    try:
        argv = [*split_command(check.command), *check.args]
        if not argv:
            raise ValueError("empty command")
        exit_code, output, timed_out = await run_captured_async(
            argv,
            resolve_command_cwd(repo, check.cwd),
            {**os.environ, **(check.env or {})},
            check.timeout,
        )
    except (OSError, ValueError) as exc:  # unlaunchable: a failed gate, not a crash
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


def run_check(
    repo: Path,
    check: Check,
    *,
    revision: str | None = None,
    fingerprint: str | None = None,
) -> CheckResult:
    """Synchronous entry point for the same owned command runner used by batches."""
    return asyncio.run(
        _run_check(repo, check, revision=revision, fingerprint=fingerprint)
    )


def checks_current(  # ruff: ignore[too-many-arguments] -- optional explicit fingerprint supports in-memory review config
    results: list[CheckResult],
    checks: list[Check],
    repo: Path,
    revision: str | None = None,
    *,
    fingerprint: str | None = None,
) -> bool:
    if not checks:
        return True
    revision = revision or code_revision(repo)
    fingerprint = fingerprint or config_fingerprint(repo)
    for check in checks:
        identity = check_identity(check)
        matches = [r for r in results if r.identity == identity]
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
    if reviewer != "PASS" or tester not in {None, "PASS"}:
        return False
    revision = code_revision(repo)
    return (
        reviewer_revision is None or reviewer_revision == revision
    ) and checks_current(results, checks, repo, revision)


async def run_final_checks(
    repo: Path, config: dict, *, fingerprint: str | None = None
) -> list[CheckResult]:
    """Run current gates after the last edit, reusing the managed test stage.

    A batch that edits existing source is re-run (its results describe code that
    no longer exists). A batch that only left new untracked output behind (a
    timestamped report) cannot be made to converge by re-running, so its
    results are re-stamped to the tree they produced.
    """
    checks = configured_checks(config)
    if not checks:
        return []
    for _attempt in range(3):
        baseline = await asyncio.to_thread(stage_baseline, repo)
        results = await _run_check_batch(repo, config, checks, fingerprint=fingerprint)
        revision = await asyncio.to_thread(code_revision, repo)
        current_fingerprint = fingerprint or config_fingerprint(repo)
        if all(
            result.revision == revision
            and result.config_fingerprint == current_fingerprint
            for result in results
        ):
            return results
        if await asyncio.to_thread(_stage_source_unchanged, repo, baseline) and all(
            r.config_fingerprint == current_fingerprint for r in results
        ):
            return [replace(result, revision=revision) for result in results]
    return results


async def _run_check_batch(  # ruff: ignore[complex-structure, too-many-branches]
    repo: Path, config: dict, checks: list[Check], *, fingerprint: str | None = None
) -> list[CheckResult]:
    results = []
    revision = await asyncio.to_thread(code_revision, repo)
    fingerprint = fingerprint or config_fingerprint(repo)
    for check in checks:
        if check.kind not in {"test", "browser"}:
            results.append(
                await _run_check(
                    repo, check, revision=revision, fingerprint=fingerprint
                )
            )
    tests = [check for check in checks if check.kind == "test"]
    browser_checks = [check for check in checks if check.kind == "browser"]
    if tests or browser_checks:
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

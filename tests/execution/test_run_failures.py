"""Retryable-by-default failure classification and the parked phase."""

import asyncio
import errno
import subprocess
from unittest.mock import patch

import pytest
from claude_agent_sdk import CLINotFoundError, ProcessError

from meow.execution import sprint_runner
from meow.execution.run_failures import (
    BLOCKING_CAUSES,
    BlockingRunError,
    InvalidConfigError,
    TransientRunError,
    classify_failure,
    next_command,
    progress_fingerprint,
)
from meow.execution.run_state import TERMINAL_PHASES, RunStateError, RunStore
from meow.execution.sprint_runner import _SETTLED_PHASES, _settle_failure
from meow.infrastructure.worktree import StartPointError
from meow.infrastructure.worktree_setup import WorktreeSetupError
from meow.project.config import load_config


class _ApiFailure(RuntimeError):
    """Duck-typed stand-in for Task 11's ApiFailure."""

    def __init__(self, kind, cause):
        super().__init__(f"api {cause}")
        self.kind = kind
        self.cause = cause


def _store(tmp_path, *, source="prompt"):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir(exist_ok=True)
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    store = RunStore(tmp_path)
    record = store.create(
        source=source, request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.transition(record.id, "generator_started")
    return store, record


def _winerror(code):
    exc = OSError(f"[WinError {code}] Insufficient system resources exist")
    exc.winerror = code
    return exc


def _invalid_config(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("max_rounds = 0\n")
    try:
        load_config(tmp_path)
    except ValueError as exc:
        return exc
    raise AssertionError("invalid config loaded")


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda _p: BlockingRunError("bad", cause="auth"), ("blocking", "auth")),
        (lambda _p: RunStateError("corrupt"), ("blocking", "invalid_run_state")),
        (_invalid_config, ("blocking", "invalid_config")),
        (lambda _p: InvalidConfigError("bad"), ("blocking", "invalid_config")),
        (lambda _p: CLINotFoundError("no cli"), ("blocking", "cli_not_found")),
        (
            lambda _p: _ApiFailure("blocking", "model_unavailable"),
            ("blocking", "model_unavailable"),
        ),
        (
            lambda _p: RuntimeError("Edit: permission denied by policy"),
            ("blocking", "policy_denied"),
        ),
        (
            lambda _p: WorktreeSetupError(
                "setup command requires approval or is denied by policy: npm",
                cause="policy_denied",
            ),
            ("blocking", "policy_denied"),
        ),
        (
            lambda _p: StartPointError("Cannot resolve start point 'x'", ref="x"),
            ("blocking", "invalid_ref"),
        ),
        (
            lambda _p: RuntimeError("fatal: not a git repository (or any parent)"),
            ("blocking", "invalid_input"),
        ),
    ],
)
def test_classify_failure_table(tmp_path, make, expected):
    kind, cause = classify_failure(make(tmp_path))
    assert (kind, cause) == expected
    assert cause in BLOCKING_CAUSES


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (MemoryError(), ("resource", "resource")),
        (_winerror(1450), ("resource", "resource")),
        (_winerror(1455), ("resource", "resource")),
        (OSError(errno.ENOMEM, "Cannot allocate memory"), ("resource", "resource")),
        (RuntimeError("boom"), ("transient", "error")),
        (ProcessError("exit 1", exit_code=1), ("transient", "error")),
        (TimeoutError(), ("transient", "error")),
        (ValueError("mid-run value"), ("transient", "error")),
        (_ApiFailure("park", "rate_limit"), ("transient", "rate_limit")),
        (TransientRunError("later", cause="budget"), ("transient", "budget")),
        (
            WorktreeSetupError("setup timed out", cause="setup_timeout"),
            ("transient", "setup_timeout"),
        ),
        (
            StartPointError("fetch failed", ref="x", transient=True),
            ("transient", "error"),
        ),
    ],
)
def test_classify_non_blocking(exc, expected):
    assert classify_failure(exc) == expected


def test_transient_run_error_carries_retry_after():
    exc = TransientRunError("rate limited", cause="rate_limit", retry_after=30.0)
    assert (exc.cause, exc.retry_after) == ("rate_limit", 30.0)
    assert TransientRunError("x", cause="c").retry_after is None
    assert isinstance(BlockingRunError("x", cause="auth"), RuntimeError)


def _fail_drive(store, record, exc):
    async def boom(*_args, **_kwargs):  # ruff: ignore[unused-async]
        raise exc

    with (
        patch.object(sprint_runner, "_drive_sprint", boom),
        pytest.raises(type(exc)),
    ):
        asyncio.run(
            sprint_runner.run_sprint(
                store.directory.parent.parent, "f", "x", run_id=record.id
            )
        )
    return store.load(record.id)


def test_winerror_1450_parks_run(tmp_path):
    store, record = _store(tmp_path)
    current = _fail_drive(store, record, _winerror(1450))
    assert current.phase == "parked"
    assert "1450" in current.last_failure
    assert current.park["kind"] == "resource"
    assert current.park["phase_before"] == "generator_started"
    assert current.park["attempts"] == 1
    assert current.results["next_command"] == next_command(current)


def test_unknown_exception_parks(tmp_path):
    store, record = _store(tmp_path)
    current = _fail_drive(store, record, RuntimeError("boom"))
    assert current.phase == "parked"
    assert current.last_failure == "boom"
    assert current.park["kind"] == "transient"
    assert current.park["cause"] == "error"


def test_blocking_cause_fails(tmp_path):
    store, record = _store(tmp_path)
    current = _fail_drive(store, record, CLINotFoundError("Claude Code not found"))
    assert current.phase == "failed"
    assert "not found" in current.last_failure
    assert current.results["failure"] == {"kind": "blocking", "cause": "cli_not_found"}
    assert "next_command" not in current.results


def test_settled_run_is_left_alone(tmp_path):
    store, record = _store(tmp_path)
    store.transition(record.id, "exhausted", last_failure="maximum rounds exhausted")
    _settle_failure(store, record.id, RuntimeError("late"))
    assert store.load(record.id).phase == "exhausted"


def test_parked_is_settled_but_not_terminal_or_active():
    from meow.execution.run_state import _ACTIVE_PHASES

    assert "parked" in _SETTLED_PHASES
    assert "parked" not in TERMINAL_PHASES
    assert "parked" not in _ACTIVE_PHASES


def test_park_stores_retry_after(tmp_path):
    store, record = _store(tmp_path)
    exc = TransientRunError("rate limited", cause="rate_limit", retry_after=120.0)
    _settle_failure(store, record.id, exc)
    park = store.load(record.id).park
    assert (park["cause"], park["retry_after"]) == ("rate_limit", 120.0)


def _park_again(store, run_id, reason="boom"):
    store.transition(run_id, "resuming")
    store.transition(run_id, "generator_started")
    return store.park(run_id, reason, "transient", "error")


def test_no_progress_budget_ends_failed(tmp_path):
    store, record = _store(tmp_path)
    assert (
        store.park(record.id, RuntimeError("boom"), "transient", "error").park[
            "attempts"
        ]
        == 1
    )
    for expected in (2, 3):
        current = _park_again(store, record.id)
        assert (current.phase, current.park["attempts"]) == ("parked", expected)
    current = _park_again(store, record.id)
    assert current.phase == "failed"
    assert current.park["cause"] == "no_progress"
    assert current.park["kind"] == "blocking"
    assert current.results["failure"] == {"kind": "blocking", "cause": "no_progress"}
    assert "boom" in current.last_failure
    assert "4" in current.last_failure
    assert "generator_started" in current.last_failure
    assert "next_command" not in current.results
    assert next_command(current) is None


def test_progress_resets_budget(tmp_path):
    store, record = _store(tmp_path)
    with patch("meow.execution.run_failures.progress_fingerprint", return_value="a"):
        store.park(record.id, "boom", "transient", "error")
        store.transition(record.id, "generator_started")
        again = store.park(record.id, "boom", "transient", "error")
        assert again.park["attempts"] == 2  # ruff: ignore[magic-value-comparison]
    store.transition(record.id, "generator_started")
    with patch("meow.execution.run_failures.progress_fingerprint", return_value="b"):
        current = store.park(record.id, "boom", "transient", "error")
    assert current.park["attempts"] == 1
    assert current.phase == "parked"


def test_progress_fingerprint_tracks_round_head_and_active_transitions(tmp_path):
    store, record = _store(tmp_path)
    first = progress_fingerprint(store.load(record.id))
    assert first == progress_fingerprint(store.load(record.id))
    store.transition(record.id, "checks_finished")  # not an active phase
    assert progress_fingerprint(store.load(record.id)) != first  # phase changed
    store.transition(record.id, "generator_started")
    second = progress_fingerprint(store.load(record.id))
    assert second == first  # replaying the same step is not progress
    store.transition(record.id, "reviewer_started")
    store.transition(record.id, "generator_started")
    assert progress_fingerprint(store.load(record.id)) != second  # a new step ran
    store.transition(record.id, "generator_started", round=2)
    assert progress_fingerprint(store.load(record.id)) != second
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        cwd=tmp_path,
        check=True,
    )
    third = progress_fingerprint(store.load(record.id))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    assert head in third


def test_park_keeps_phase_before_across_parks(tmp_path):
    store, record = _store(tmp_path)
    store.park(record.id, "boom", "transient", "error")
    current = store.park(record.id, "again", "transient", "error")
    assert current.park["phase_before"] == "generator_started"
    assert current.last_failure == "again"


def test_active_transition_drops_next_command_but_keeps_park(tmp_path):
    store, record = _store(tmp_path)
    parked = store.park(record.id, "boom", "transient", "error")
    assert parked.results["next_command"]
    resumed = store.transition(record.id, "resuming")
    assert "next_command" not in resumed.results
    assert resumed.park == parked.park
    assert resumed.last_failure is None


def test_next_command_shape(tmp_path):
    store, record = _store(tmp_path)
    parked = store.park(record.id, "boom", "transient", "error")
    assert next_command(parked) == (
        f"meow resume {record.id} --continue --work-dir {tmp_path.resolve()}"
    )
    native_dir = tmp_path / "native repo"
    native_dir.mkdir()
    native_store, native = _store(native_dir, source="native")
    native = native_store.park(native.id, "boom", "transient", "error")
    assert next_command(native) == (
        f"meow resume {native.id} --continue --repair "
        f'--work-dir "{native_dir.resolve()}"'
    )
    for phase in ("complete", "failed", "delivered"):
        store.transition(record.id, phase)
        assert next_command(store.load(record.id)) is None
    lint = store.create(
        source="lint-fix", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    assert next_command(lint) is None


def test_load_rejects_non_dict_park(tmp_path):
    store, record = _store(tmp_path)
    path = store.directory / f"{record.id}.json"
    path.write_text(path.read_text().replace('"park": {}', '"park": []'))
    with pytest.raises(RunStateError):
        store.load(record.id)


def test_park_reads_auto_retries_from_config(tmp_path):
    store, record = _store(tmp_path)
    (tmp_path / ".meow" / "config.toml").write_text("[run]\nauto_retries = 1\n")
    with patch("meow.execution.run_failures.progress_fingerprint", return_value="a"):
        store.park(record.id, "boom", "transient", "error")
        store.transition(record.id, "generator_started")
        current = store.park(record.id, "boom", "transient", "error")
    assert current.phase == "failed"
    assert current.park["cause"] == "no_progress"

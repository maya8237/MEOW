"""`meow native orchestrate ...`: JSON contracts of the in-session helpers."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from meow.execution.orchestration.integration import MergeOutcome
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import (
    DriverBusyError,
    DriverLock,
    OrchestratorStore,
)
from meow.execution.orchestration.tick import ConflictInfo, FailureInfo, TickResult
from meow.execution.run_state import CheckResult, RunStore
from meow.infrastructure.worktree import DirtyWorkingTreeError
from meow.native import native_orchestrate as orch

PARALLEL = 2
WAIT_TIMEOUT = 0.1
EXIT_NO_PR = 3
PASS_VERDICT = "SUMMARY: all good\nSTATUS: PASS\n"
FAIL_VERDICT = "SUMMARY: not good\nSTATUS: FAIL\n"


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def write_tasks(directory: Path, entries: list[dict]) -> Path:
    path = directory / "tasks.json"
    path.write_text(json.dumps({"tasks": entries}), encoding="utf-8")
    return path


def two_task_file(directory: Path) -> Path:
    return write_tasks(
        directory,
        [
            {"id": "a", "request": "Do a"},
            {"id": "b", "request": "Do b", "depends_on": ["a"]},
        ],
    )


def started(repo: Path) -> tuple[str, OrchestratorStore]:
    """An orchestrator in phase running with tasks a and b."""
    result = orch.init(repo, tasks_file=two_task_file(repo.parent))
    return result["id"], OrchestratorStore(repo)


def lock_for(store: OrchestratorStore, orch_id: str, mode: str = "native"):
    return DriverLock(store.dir_for(orch_id), mode=mode)


def edit(store: OrchestratorStore, orch_id: str, **fields):
    """Set record attributes; task fields go through `tasks={"a": {...}}`."""
    tasks = fields.pop("tasks", {})

    def mutate(record):
        for key, value in fields.items():
            setattr(record, key, value)
        for task_id, changes in tasks.items():
            for key, value in changes.items():
                setattr(record.task(task_id), key, value)

    return store.update(orch_id, mutate)


def fake_run_tick(result: TickResult, calls: list | None = None):
    async def run(*args, launcher):
        await asyncio.sleep(0)
        if calls is not None:
            calls.append({"orch_id": args[2], "launcher": launcher})
        return result

    return run


def merged_orchestrator(repo: Path) -> tuple[str, OrchestratorStore]:
    orch_id, store = started(repo)
    edit(
        store,
        orch_id,
        tasks={"a": {"status": "merged"}, "b": {"status": "merged"}},
    )
    return orch_id, store


def no_checks(monkeypatch, results=()):
    async def run(worktree, config):
        await asyncio.sleep(0)
        return list(results)

    monkeypatch.setattr(orch, "run_final_checks", run)


def failing_check() -> CheckResult:
    return CheckResult(
        kind="lint",
        command="ruff check",
        required=True,
        exit_code=1,
        duration=0.1,
        output="E501 line too long",
        revision="r",
        config_fingerprint="c",
    )


# --- init and tasks ---------------------------------------------------------


def test_init_objective_returns_decomposer_prompt(git_repo):
    repo, _ = git_repo

    result = orch.init(repo, objective="Build login")

    assert set(result) == {
        "id",
        "integration_worktree",
        "state_file",
        "tasks_file",
        "decomposer",
    }
    assert set(result["decomposer"]) == {"system_prompt", "query", "output_file"}
    assert result["decomposer"]["query"] == "Objective:\nBuild login"
    assert result["decomposer"]["output_file"] == result["tasks_file"]
    store = OrchestratorStore(repo)
    record = store.load(result["id"])
    assert (record.mode, record.phase, record.source) == (
        "native",
        "decomposing",
        "objective",
    )
    assert Path(result["integration_worktree"]).is_dir()
    assert lock_for(store, record.id).holder()["mode"] == "native"


def test_init_with_plain_tasks_file_skips_the_decomposer(git_repo):
    repo, _ = git_repo

    result = orch.init(repo, tasks_file=two_task_file(repo.parent))

    assert result["decomposer"] is None
    record = OrchestratorStore(repo).load(result["id"])
    assert record.phase == "running"
    assert record.source == "tasks"
    assert record.objective == "Run the tasks in tasks.json"
    assert [task.id for task in record.tasks] == ["a", "b"]


def test_init_with_jira_entries_needs_the_decomposer(git_repo):
    repo, _ = git_repo
    path = write_tasks(
        repo.parent,
        [
            {
                "id": "PROJ-1",
                "request": "Resolve Jira issue PROJ-1: x",
                "source_ref": "PROJ-1",
            },
            {
                "id": "PROJ-2",
                "request": "Resolve Jira issue PROJ-2: y",
                "source_ref": "PROJ-2",
            },
        ],
    )

    result = orch.init(repo, tasks_file=path)

    assert result["decomposer"] is not None
    record = OrchestratorStore(repo).load(result["id"])
    assert (record.phase, record.source) == ("decomposing", "jira")
    assert record.intake["jira_keys"] == ["PROJ-1", "PROJ-2"]
    assert record.objective == "Resolve Jira issues PROJ-1, PROJ-2"


def test_init_rejects_a_tasks_file_mixing_jira_and_plain_entries(git_repo):
    repo, _ = git_repo
    path = write_tasks(
        repo.parent,
        [
            {"id": "a", "request": "x", "source_ref": "PROJ-1"},
            {"id": "b", "request": "y"},
        ],
    )

    with pytest.raises(ValueError, match="every task a source_ref"):
        orch.init(repo, tasks_file=path)


@pytest.mark.parametrize("both", [True, False])
def test_init_needs_exactly_one_source(git_repo, both):
    repo, _ = git_repo
    extra = {"objective": "x", "tasks_file": repo.parent / "t.json"} if both else {}

    with pytest.raises(ValueError, match="exactly one of"):
        orch.init(repo, **extra)


def test_init_refuses_a_dirty_tree(git_repo):
    repo, _ = git_repo
    (repo / "dirty.txt").write_text("x", encoding="utf-8")

    with pytest.raises(DirtyWorkingTreeError):
        orch.init(repo, objective="Build login")


def test_init_records_the_options(git_repo):
    repo, _ = git_repo

    result = orch.init(
        repo, objective="Build login", max_parallel=PARALLEL, test=True, no_pr=True
    )

    record = OrchestratorStore(repo).load(result["id"])
    assert record.max_parallel == PARALLEL
    assert record.options == {"test": True, "pr": False}


def test_save_tasks_accepts_valid_output(git_repo):
    repo, _ = git_repo
    result = orch.init(repo, objective="Build login")
    Path(result["tasks_file"]).write_text(
        json.dumps({"tasks": [{"id": "api", "request": "Build the API"}]}),
        encoding="utf-8",
    )

    saved = orch.save_tasks(repo, result["id"], Path(result["tasks_file"]))

    assert saved == {"ok": True, "tasks": 1}
    record = OrchestratorStore(repo).load(result["id"])
    assert (record.phase, [t.id for t in record.tasks]) == ("running", ["api"])


def test_save_tasks_allows_one_corrective_retry_then_fails(git_repo):
    repo, _ = git_repo
    result = orch.init(repo, objective="Build login")
    bad = Path(result["tasks_file"])
    bad.write_text("not json", encoding="utf-8")

    first = orch.save_tasks(repo, result["id"], bad)
    second = orch.save_tasks(repo, result["id"], bad)

    assert first["ok"] is False and first["retry_allowed"] is True
    assert second["ok"] is False and second["retry_allowed"] is False
    record = OrchestratorStore(repo).load(result["id"])
    assert record.phase == "failed"
    assert record.error["phase"] == "decomposing"
    with pytest.raises(ValueError, match="is failed, not decomposing"):
        orch.save_tasks(repo, result["id"], bad)


def test_save_tasks_flags_a_file_outside_the_given_output_path(git_repo):
    repo, _ = git_repo
    result = orch.init(repo, objective="Build login")
    wrong = write_tasks(repo.parent, [{"id": "api", "request": "Build API"}])

    saved = orch.save_tasks(repo, result["id"], wrong)

    assert saved["ok"] is True
    assert result["tasks_file"] in saved["warning"]


def test_save_tasks_missing_output_allows_corrective_retry(git_repo):
    repo, _ = git_repo
    result = orch.init(repo, objective="Build login")

    saved = orch.save_tasks(repo, result["id"], Path(result["tasks_file"]))

    assert saved["ok"] is False
    assert saved["retry_allowed"] is True


# --- tick -------------------------------------------------------------------


def test_tick_json_contract(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store = started(repo)
    sentinel = object()
    calls = []
    monkeypatch.setattr(orch, "LAUNCHER", sentinel)
    result = TickResult("waiting", launched=("a",), running=("a",))
    monkeypatch.setattr(orch, "run_tick", fake_run_tick(result, calls))

    payload = orch.tick(repo, orch_id)

    assert payload == {
        "state": "waiting",
        "launched": ["a"],
        "merged": [],
        "running": ["a"],
        "conflicts": [],
        "failures": [],
    }
    assert json.dumps(payload)
    assert calls[0]["launcher"] is sentinel


def test_tick_conflict_carries_the_integrator_prompt(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store = started(repo)
    info = ConflictInfo("b", ("shared.txt",), "Do b", ("Do a",))
    monkeypatch.setattr(
        orch, "run_tick", fake_run_tick(TickResult("conflicts", conflicts=(info,)))
    )

    payload = orch.tick(repo, orch_id)

    conflict = payload["conflicts"][0]
    assert set(conflict) == {"task", "paths", "prompt"}
    assert (conflict["task"], conflict["paths"]) == ("b", ["shared.txt"])
    assert set(conflict["prompt"]) == {"system_prompt", "query"}
    assert "shared.txt" in conflict["prompt"]["query"]
    assert "Do a" in conflict["prompt"]["query"]
    assert "Do b" in conflict["prompt"]["query"]


def test_tick_failure_carries_the_triage_prompt_and_evidence(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    edit(store, orch_id, tasks={"a": {"status": "failed"}})
    info = FailureInfo("a", "child-failed", ("/tmp/run.json",))
    monkeypatch.setattr(
        orch, "run_tick", fake_run_tick(TickResult("failures", failures=(info,)))
    )

    payload = orch.tick(repo, orch_id)

    failure = payload["failures"][0]
    assert (failure["task"], failure["cause"]) == ("a", "child-failed")
    assert failure["evidence"] == ["/tmp/run.json"]
    assert set(failure["prompt"]) == {"system_prompt", "query", "output_file"}
    assert Path(failure["prompt"]["output_file"]).name == "decision-a-1.json"
    assert "Do a" in failure["prompt"]["query"]


def test_tick_refuses_an_orchestrator_without_tasks(git_repo):
    repo, _ = git_repo
    result = orch.init(repo, objective="Build login")

    with pytest.raises(ValueError, match="is decomposing, not running or blocked"):
        orch.tick(repo, result["id"])


def test_tick_releases_the_lock_when_blocked(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    monkeypatch.setattr(orch, "run_tick", fake_run_tick(TickResult("blocked")))

    orch.tick(repo, orch_id)

    assert lock_for(store, orch_id).holder() is None


def test_a_live_cli_driver_blocks_native_helpers(git_repo):
    repo, _ = git_repo
    store = OrchestratorStore(repo)
    record = store.create(
        objective="x",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )
    assert lock_for(store, record.id, "cli").acquire()

    with pytest.raises(DriverBusyError, match="live cli driver"):
        orch.tick(repo, record.id)
    with pytest.raises(DriverBusyError, match="live CLI driver"):
        orch.release(repo, record.id)


# --- wait -------------------------------------------------------------------


def running_child(repo: Path, store: OrchestratorStore, orch_id: str) -> Path:
    run = RunStore(repo).create(
        source="orchestrator",
        request="Do a",
        repo=repo,
        worktree=repo,
        branch="orchestrator/x-a",
    )
    edit(
        store,
        orch_id,
        tasks={"a": {"status": "running", "run_ids": [run.id]}},
    )
    return RunStore(repo).directory / f"{run.id}.json"


def test_wait_returns_when_a_child_journal_changes(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    journal = running_child(repo, store, orch_id)
    monkeypatch.setattr(orch, "POLL_SECONDS", 0.01)

    def touch(_seconds):
        stamp = journal.stat().st_mtime_ns + 5_000_000_000
        os.utime(journal, ns=(stamp, stamp))

    monkeypatch.setattr(orch.time, "sleep", touch)

    assert orch.wait(repo, orch_id, timeout=30)["changed"] is True


def test_wait_returns_when_the_cancel_marker_appears(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    monkeypatch.setattr(orch, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(
        orch.time,
        "sleep",
        lambda _seconds: store.cancel_marker(orch_id).write_text("1", encoding="utf-8"),
    )

    assert orch.wait(repo, orch_id, timeout=30)["changed"] is True


def test_wait_times_out(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    running_child(repo, store, orch_id)
    monkeypatch.setattr(orch, "POLL_SECONDS", 0.02)

    payload = orch.wait(repo, orch_id, timeout=WAIT_TIMEOUT)

    assert payload["changed"] is False
    assert payload["elapsed"] >= WAIT_TIMEOUT


# --- merges and triage ------------------------------------------------------


def test_merge_helpers_refuse_a_task_that_is_not_mid_merge(git_repo):
    repo, _ = git_repo
    orch_id, _store = started(repo)

    with pytest.raises(ValueError, match="not mid-merge"):
        orch.merge_done(repo, orch_id, "a")
    with pytest.raises(ValueError, match="not mid-merge"):
        orch.merge_abort(repo, orch_id, "a")


def mid_merge(repo: Path):
    orch_id, store = started(repo)
    store.update(orch_id, lambda record: record.integration.update(merging="a"))
    return orch_id, store


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (MergeOutcome("merged", "abc"), ("merged", "abc", [])),
        (
            MergeOutcome("conflict", None, ("shared.txt",)),
            ("conflict", None, ["shared.txt"]),
        ),
    ],
)
def test_merge_done_reports_the_outcome(git_repo, monkeypatch, outcome, expected):
    repo, _ = git_repo
    orch_id, _store = mid_merge(repo)

    async def finish(*args):
        await asyncio.sleep(0)
        return outcome

    monkeypatch.setattr(orch, "finish_merge", finish)

    payload = orch.merge_done(repo, orch_id, "a")

    assert payload == {
        "task": "a",
        "status": expected[0],
        "commit": expected[1],
        "paths": expected[2],
    }


def test_merge_done_records_an_integration_failure_when_checks_fail(
    git_repo, monkeypatch
):
    repo, _ = git_repo
    orch_id, store = mid_merge(repo)
    log = store.dir_for(orch_id) / "integration-a-1.log"
    log.write_text("checks failed", encoding="utf-8")
    failures = []

    async def finish(*args):
        await asyncio.sleep(0)
        return MergeOutcome("checks_failed", log=log)

    monkeypatch.setattr(orch, "finish_merge", finish)
    monkeypatch.setattr(
        orch, "record_failure", lambda record, task_id, **kw: failures.append(kw)
    )

    payload = orch.merge_done(repo, orch_id, "a")

    assert payload["status"] == "checks_failed"
    assert failures == [
        {"cause": "integration", "evidence": [str(log)], "max_cycles": 3}
    ]


def test_merge_abort_rolls_back_and_records_an_integration_failure(
    git_repo, monkeypatch
):
    repo, _ = git_repo
    orch_id, store = mid_merge(repo)
    log = store.dir_for(orch_id) / "integration-a-1.log"
    log.write_text("conflict", encoding="utf-8")
    aborted, failures = [], []
    monkeypatch.setattr(orch, "abort_merge", lambda *args: aborted.append(args[-1]))
    monkeypatch.setattr(
        orch, "record_failure", lambda record, task_id, **kw: failures.append(kw)
    )

    payload = orch.merge_abort(repo, orch_id, "a")

    assert payload == {"task": "a", "status": "aborted"}
    assert aborted == ["a"]
    assert failures[0]["cause"] == "integration"
    assert failures[0]["evidence"] == [str(log)]


def decision_file(directory: Path, text: str) -> Path:
    path = directory / "decision.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_triage_applies_a_valid_decision(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    edit(store, orch_id, tasks={"a": {"status": "failed"}})
    seen = {}

    def apply(repo_dir, record, task_id, decision, **kwargs):
        seen.update(action=decision.action, **kwargs)
        record.task(task_id).status = "pending"
        return [task_id]

    monkeypatch.setattr(orch, "apply_decision", apply)
    path = decision_file(repo.parent, '{"action": "retry", "rationale": "flaky"}')

    payload = orch.triage(repo, orch_id, "a", path)

    assert payload == {
        "task": "a",
        "action": "retry",
        "status": "pending",
        "pending": ["a"],
        "phase": "running",
    }
    assert seen == {"action": "retry", "max_cycles": 3}


def test_triage_blocks_on_an_invalid_decision(git_repo):
    repo, _ = git_repo
    orch_id, store = started(repo)
    edit(store, orch_id, tasks={"a": {"status": "failed"}})
    path = decision_file(repo.parent, "not json")

    payload = orch.triage(repo, orch_id, "a", path)

    assert (payload["action"], payload["status"], payload["phase"]) == (
        "invalid",
        "blocked",
        "blocked",
    )
    record = store.load(orch_id)
    assert record.blocker["cause"] == "invalid-decision"
    assert record.blocker["task_id"] == "a"
    assert record.blocker["evidence"] == [str(path)]


def test_triage_needs_a_failed_task_that_exists(git_repo):
    repo, _ = git_repo
    orch_id, _store = started(repo)
    path = decision_file(repo.parent, '{"action": "retry", "rationale": "x"}')

    with pytest.raises(ValueError, match="task a is pending, not awaiting triage"):
        orch.triage(repo, orch_id, "a", path)
    with pytest.raises(ValueError, match="has no task 'zzz'"):
        orch.triage(repo, orch_id, "zzz", path)


# --- objective review -------------------------------------------------------


def test_review_returns_the_prompt_and_runs_the_checks(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = merged_orchestrator(repo)
    no_checks(monkeypatch)

    payload = orch.review(repo, orch_id)

    assert set(payload) == {
        "round",
        "max_rounds",
        "checks_passed",
        "check_failures",
        "system_prompt",
        "query",
        "review_file",
        "model",
        "configured_model",
        "host",
        "model_resolution",
        "model_fallback_reason",
        "tools",
    }
    assert (payload["round"], payload["checks_passed"]) == (1, True)
    assert payload["review_file"].endswith(".review.md")
    record = store.load(orch_id)
    assert record.phase == "reviewing"
    assert record.review["round"] == 1
    assert record.review["review_file"] == payload["review_file"]


def test_review_reports_failing_required_checks(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store = merged_orchestrator(repo)
    no_checks(monkeypatch, [failing_check()])

    payload = orch.review(repo, orch_id)

    assert payload["checks_passed"] is False
    assert payload["check_failures"][0]["command"] == "ruff check"
    assert payload["check_failures"][0]["exit_code"] == 1


def test_review_needs_every_task_merged(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    edit(store, orch_id, tasks={"a": {"status": "merged"}})
    no_checks(monkeypatch)

    with pytest.raises(ValueError, match="tasks are not merged yet: b"):
        orch.review(repo, orch_id)


def reviewed(repo: Path, monkeypatch, verdict_text: str, results=()):
    orch_id, store = merged_orchestrator(repo)
    no_checks(monkeypatch, results)
    payload = orch.review(repo, orch_id)
    verdict = Path(payload["review_file"])
    verdict.parent.mkdir(parents=True, exist_ok=True)
    verdict.write_text(verdict_text, encoding="utf-8")
    return orch_id, store, verdict


def test_review_result_pass_moves_to_delivering(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store, verdict = reviewed(repo, monkeypatch, PASS_VERDICT)

    payload = orch.review_result(repo, orch_id, verdict)

    assert payload == {
        "verdict": "PASS",
        "round": 1,
        "rounds_left": 2,
        "phase": "delivering",
    }
    assert store.load(orch_id).review["verdict"] == "PASS"


def test_review_result_fail_keeps_reviewing_while_rounds_remain(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store, verdict = reviewed(repo, monkeypatch, FAIL_VERDICT)

    payload = orch.review_result(repo, orch_id, verdict)

    assert payload["verdict"] == "FAIL"
    assert (payload["rounds_left"], payload["phase"]) == (2, "reviewing")


def test_review_result_blocks_after_the_last_round(git_repo, monkeypatch):
    repo, _ = git_repo
    config = repo / ".meow" / "config.local.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text("[orchestrator]\nreview_rounds = 1\n", encoding="utf-8")
    orch_id, store, verdict = reviewed(repo, monkeypatch, FAIL_VERDICT)

    payload = orch.review_result(repo, orch_id, verdict)

    assert (payload["rounds_left"], payload["phase"]) == (0, "blocked")
    record = store.load(orch_id)
    assert record.blocker["cause"] == "objective-review"
    assert record.blocker["evidence"] == [str(verdict)]


def test_review_result_failing_checks_force_a_fail(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store, verdict = reviewed(
        repo, monkeypatch, PASS_VERDICT, [failing_check()]
    )

    payload = orch.review_result(repo, orch_id, verdict)

    assert payload["verdict"] == "FAIL"
    assert "STATUS: FAIL" in verdict.read_text(encoding="utf-8")
    assert "ruff check" in verdict.read_text(encoding="utf-8")


def test_review_result_missing_file_is_a_fail(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store, verdict = reviewed(repo, monkeypatch, PASS_VERDICT)
    verdict.unlink()

    assert orch.review_result(repo, orch_id, verdict)["verdict"] == "FAIL"


def test_review_result_needs_a_review_round_first(git_repo):
    repo, _ = git_repo
    orch_id, _store = merged_orchestrator(repo)

    with pytest.raises(ValueError, match="is running, not reviewing"):
        orch.review_result(repo, orch_id, repo.parent / "none.md")


# --- delivery ---------------------------------------------------------------


@pytest.mark.parametrize("change", ["commit", "edit", "config", "report"])
def test_review_result_rejects_stale_or_unrelated_evidence(
    git_repo, monkeypatch, change
):
    repo, _ = git_repo
    orch_id, store, verdict = reviewed(repo, monkeypatch, PASS_VERDICT)
    worktree = Path(store.load(orch_id).integration["worktree"])
    if change in {"commit", "edit"}:
        (worktree / "new-code.py").write_text("broken = True\n")
        if change == "commit":
            git(worktree, "add", "new-code.py")
            git(worktree, "commit", "-m", "unreviewed")
    elif change == "config":
        (repo / ".meow" / "config.local.toml").write_text(
            "[orchestrator]\nreview_rounds = 2\n"
        )
    else:
        verdict = repo.parent / "unrelated.review.md"
        verdict.write_text(PASS_VERDICT)
    with pytest.raises(ValueError, match="review"):
        orch.review_result(repo, orch_id, verdict)
    assert store.load(orch_id).phase == "reviewing"


def test_delivery_rejects_code_changed_after_passing_review(git_repo, monkeypatch):
    repo, remote = git_repo
    orch_id, store, verdict = reviewed(repo, monkeypatch, PASS_VERDICT)
    orch.review_result(repo, orch_id, verdict)
    worktree = Path(store.load(orch_id).integration["worktree"])
    (worktree / "late.py").write_text("broken = True\n")
    with pytest.raises(RuntimeError, match="review"):
        orch.deliver(repo, orch_id)
    branch = store.load(orch_id).integration["branch"]
    assert not git(remote, "branch", "--list", branch)


@pytest.mark.parametrize("change", ["tracked", "staged", "untracked"])
def test_delivery_rejects_reviewed_source_absent_from_head(
    git_repo, monkeypatch, change
):
    repo, _ = git_repo
    orch_id, store = merged_orchestrator(repo)
    no_checks(monkeypatch)
    worktree = Path(store.load(orch_id).integration["worktree"])
    source = "new.py" if change == "untracked" else "README.md"
    (worktree / source).write_text("reviewed but absent from HEAD\n")
    if change == "staged":
        git(worktree, "add", source)
    payload = orch.review(repo, orch_id)
    verdict = Path(payload["review_file"])
    verdict.parent.mkdir(parents=True, exist_ok=True)
    verdict.write_text(PASS_VERDICT)
    orch.review_result(repo, orch_id, verdict)
    with pytest.raises(RuntimeError, match="commit"):
        orch.deliver(repo, orch_id)
    assert store.load(orch_id).phase == "reviewing"


def test_review_resolves_host_model(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _ = merged_orchestrator(repo)
    no_checks(monkeypatch)
    payload = orch.review(repo, orch_id, host="codex", available_models=["gpt-6"])
    assert payload["host"] == "codex"
    assert payload["model"] is None


def test_cancel_during_final_checks_does_not_start_review(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = merged_orchestrator(repo)

    async def cancel_in_checks(worktree, config):  # ruff: ignore[unused-async]
        store.cancel_marker(orch_id).write_text("cancel requested")
        return []

    monkeypatch.setattr(orch, "run_final_checks", cancel_in_checks)
    with pytest.raises(ValueError, match="cancelled"):
        orch.review(repo, orch_id)
    assert store.load(orch_id).phase == "cancelled"


def delivering(repo: Path):
    orch_id, store = merged_orchestrator(repo)
    edit(store, orch_id, objective="Add login")
    payload = orch.review(repo, orch_id)
    verdict = Path(payload["review_file"])
    verdict.parent.mkdir(parents=True, exist_ok=True)
    verdict.write_text(PASS_VERDICT)
    orch.review_result(repo, orch_id, verdict)
    return orch_id, store


def test_deliver_pushes_and_describes_the_pull_request(git_repo):
    repo, remote = git_repo
    orch_id, store = delivering(repo)

    payload = orch.deliver(repo, orch_id)

    record = store.load(orch_id)
    branch = f"orchestrator/{orch_id}"
    assert payload["branch"] == branch
    assert payload["base"] == record.base_branch
    assert payload["title"] == "Orchestrator: Add login"
    assert payload["body"]
    assert (payload["provider"], payload["open_pr"]) == (None, False)
    assert "no GitHub or GitLab remote" in payload["skip_reason"]
    assert record.delivery["pushed_commit"] == git(remote, "rev-parse", branch)
    assert record.phase == "delivering"


def test_deliver_describes_a_github_pull_request(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store = delivering(repo)
    git(repo, "remote", "set-url", "origin", "https://github.com/o/r.git")
    monkeypatch.setattr(orch, "push_integration", lambda *args: "sha")

    payload = orch.deliver(repo, orch_id)

    assert (payload["provider"], payload["open_pr"], payload["skip_reason"]) == (
        "github",
        True,
        None,
    )
    assert payload["manual_link"].startswith("https://github.com/o/r/compare/")


def test_deliver_skips_the_pull_request_when_disabled(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = delivering(repo)
    edit(store, orch_id, options={"test": False, "pr": False})
    git(repo, "remote", "set-url", "origin", "https://github.com/o/r.git")
    monkeypatch.setattr(orch, "push_integration", lambda *args: "sha")

    payload = orch.deliver(repo, orch_id)

    assert payload["open_pr"] is False
    assert "--no-pr" in payload["skip_reason"]


def test_deliver_push_failure_fails_the_orchestrator(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = delivering(repo)

    def rejected(*args):
        raise RuntimeError("push rejected")

    monkeypatch.setattr(orch, "push_integration", rejected)

    with pytest.raises(RuntimeError, match="push rejected"):
        orch.deliver(repo, orch_id)

    record = store.load(orch_id)
    assert record.phase == "failed"
    assert record.error == {"phase": "delivering", "message": "push rejected"}


def test_deliver_needs_a_passing_review(git_repo):
    repo, _ = git_repo
    orch_id, _store = merged_orchestrator(repo)

    with pytest.raises(ValueError, match="is running, not delivering"):
        orch.deliver(repo, orch_id)


def test_record_pr_url_delivers_and_releases_the_lock(git_repo):
    repo, _ = git_repo
    orch_id, store = delivering(repo)

    payload = orch.record_pr(repo, orch_id, url="https://x/1")

    assert (payload["phase"], payload["exit_code"]) == ("delivered", 0)
    record = store.load(orch_id)
    assert record.delivery["pr"]["url"] == "https://x/1"
    assert Path(payload["report"]).is_file()
    assert lock_for(store, orch_id).holder() is None


def test_record_pr_error_delivers_with_exit_code_3(git_repo):
    repo, _ = git_repo
    orch_id, store = delivering(repo)

    payload = orch.record_pr(repo, orch_id, error="provider refused")

    assert payload["exit_code"] == EXIT_NO_PR
    assert store.load(orch_id).delivery["pr_error"] == "provider refused"


def test_record_pr_is_idempotent_once_delivered(git_repo):
    repo, _ = git_repo
    orch_id, store = delivering(repo)
    first = orch.record_pr(repo, orch_id, url="https://x/1")

    second = orch.record_pr(repo, orch_id, url="https://x/2")

    assert second == {"phase": "delivered", "exit_code": first["exit_code"]}
    assert store.load(orch_id).delivery["pr"]["url"] == "https://x/1"


def test_record_pr_needs_exactly_one_outcome(git_repo):
    repo, _ = git_repo
    orch_id, _store = delivering(repo)

    with pytest.raises(ValueError, match="exactly one of --url or --error"):
        orch.record_pr(repo, orch_id)
    with pytest.raises(ValueError, match="exactly one of --url or --error"):
        orch.record_pr(repo, orch_id, url="u", error="e")


# --- status, cancel, release, resume ---------------------------------------


def test_status_returns_the_journal_and_a_summary(git_repo):
    repo, _ = git_repo
    orch_id, _store = started(repo)

    payload = orch.status(repo, orch_id)

    assert payload["id"] == orch_id
    assert [task["id"] for task in payload["tasks"]] == ["a", "b"]
    assert payload["summary"]
    assert json.dumps(payload)
    assert orch.status(repo)["id"] == orch_id


def test_cancel_applies_the_marker_and_reports_the_phase(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    monkeypatch.setattr(
        orch, "cancel_orchestrator", lambda repo_dir, oid: store.load(oid)
    )

    async def apply_marker(*args, launcher):
        await asyncio.sleep(0)
        store.update(args[2], lambda record: setattr(record, "phase", "cancelled"))
        return TickResult("cancelled")

    monkeypatch.setattr(orch, "run_tick", apply_marker)

    payload = orch.cancel(repo, orch_id)

    assert payload == {"id": orch_id, "phase": "cancelled", "cancelled": True}
    assert lock_for(store, orch_id).holder() is None


def test_cancel_refuses_a_finished_orchestrator(git_repo):
    repo, _ = git_repo
    orch_id, store = started(repo)
    edit(store, orch_id, phase="delivered")

    with pytest.raises(ValueError, match="already delivered"):
        orch.cancel(repo, orch_id)


def test_release_drops_the_native_lock(git_repo):
    repo, _ = git_repo
    orch_id, store = started(repo)
    assert lock_for(store, orch_id).holder() is not None

    assert orch.release(repo, orch_id) == {"id": orch_id, "released": True}

    assert lock_for(store, orch_id).holder() is None


def blocked(repo: Path, monkeypatch, cause: str = "recovery-exhausted"):
    orch_id, store = started(repo)
    monkeypatch.setattr(orch, "reset_mid_merge", lambda *args: None)
    edit(
        store,
        orch_id,
        phase="blocked",
        blocker={"task_id": "a", "cause": cause, "evidence": [], "needed": "x"},
        tasks={"a": {"status": "blocked", "cycles": 3}},
    )
    return orch_id, store


def test_resume_resets_the_blocked_task_and_replaces_its_request(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = blocked(repo, monkeypatch)

    payload = orch.resume(repo, orch_id, request="Do a differently")

    assert (payload["phase"], payload["decomposer"]) == ("running", None)
    record = store.load(orch_id)
    task = record.task("a")
    assert (task.status, task.cycles, task.request) == (
        "pending",
        0,
        "Do a differently",
    )
    assert record.blocker is None


def test_resume_of_an_objective_review_block_returns_to_reviewing(
    git_repo, monkeypatch
):
    repo, _ = git_repo
    orch_id, store = blocked(repo, monkeypatch, "objective-review")
    edit(store, orch_id, blocker={"task_id": None, "cause": "objective-review"})
    store.update(orch_id, lambda record: record.review.update(round=3))

    payload = orch.resume(repo, orch_id)

    assert payload["phase"] == "reviewing"
    assert store.load(orch_id).review["round"] == 0


def test_resume_of_a_failed_orchestrator_returns_to_the_failed_phase(
    git_repo, monkeypatch
):
    repo, _ = git_repo
    orch_id, store = started(repo)
    monkeypatch.setattr(orch, "reset_mid_merge", lambda *args: None)
    edit(store, orch_id, phase="failed", error={"phase": "delivering", "message": "x"})

    assert orch.resume(repo, orch_id)["phase"] == "delivering"
    assert store.load(orch_id).error is None


def test_resume_retries_a_pull_request_that_failed(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    monkeypatch.setattr(orch, "reset_mid_merge", lambda *args: None)
    edit(store, orch_id, phase="delivered")
    store.update(orch_id, lambda record: record.delivery.update(pr_error="boom"))

    assert orch.resume(repo, orch_id)["phase"] == "delivering"
    assert store.load(orch_id).delivery["pr_error"] is None


def test_resume_reports_the_merge_it_reset(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, _store = started(repo)
    monkeypatch.setattr(orch, "reset_mid_merge", lambda *args: "a")

    assert orch.resume(repo, orch_id)["reset_merge"] == "a"


def test_resume_of_an_unfinished_decomposition_returns_the_decomposer(git_repo):
    repo, _ = git_repo
    result = orch.init(repo, objective="Build login")
    orch.release(repo, result["id"])

    payload = orch.resume(repo, result["id"])

    assert payload["phase"] == "decomposing"
    assert set(payload["decomposer"]) == {"system_prompt", "query", "output_file"}


def test_resume_rejects_cancelled_and_misplaced_flags(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = started(repo)
    monkeypatch.setattr(orch, "reset_mid_merge", lambda *args: None)

    with pytest.raises(ValueError, match="apply only to a blocked orchestrator"):
        orch.resume(repo, orch_id, task_id="a")
    edit(store, orch_id, phase="cancelled")
    with pytest.raises(ValueError, match="cancelled orchestrators cannot be resumed"):
        orch.resume(repo, orch_id)


def test_resume_request_needs_a_task_when_the_blocker_names_none(git_repo, monkeypatch):
    repo, _ = git_repo
    orch_id, store = blocked(repo, monkeypatch, "objective-review")
    edit(store, orch_id, blocker={"task_id": None, "cause": "objective-review"})

    with pytest.raises(ValueError, match="--request needs a blocked task"):
        orch.resume(repo, orch_id, request="x")


def test_unused_task_model_is_importable():
    assert OrchestratorTask("a", "r").status == "pending"

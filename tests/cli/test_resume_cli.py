"""Resume is inspect-first and validates saved identity before mutation."""

import asyncio
import json
import os
import subprocess
import time
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli.cli import cli_main
from meow.cli.resume_cli import _validate, resume, resume_parked
from meow.common.process import process_identity as _process_identity
from meow.execution.queue_state import QueueStore, QueueWorkerLock
from meow.execution.run_state import RunStore
from meow.infrastructure.checks import config_fingerprint
from meow.infrastructure.supervisor import EXIT_BLOCKED, EXIT_TEMPFAIL
from meow.project.config import run_config


def _record(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n', encoding="utf-8"
    )
    plan = tmp_path / "plan.md"
    plan.write_text("plan", encoding="utf-8")
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.transition(record.id, "interrupted_mutation", plan_file=str(plan))
    return store, record


def test_inspect_only_does_not_start_agents(tmp_path, capsys):
    _, record = _record(tmp_path)
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id)) == 0
    run.assert_not_awaited()
    assert "interrupted_mutation" in capsys.readouterr().out


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("phase", ["interrupted_mutation", "cancelled", "delivered"])
def test_resume_refuses_live_background_worker(tmp_path, capsys, registered, phase):
    store, record = _record(tmp_path)
    store.transition(record.id, phase)
    metadata = {"launch_started_at": time.time()}
    if registered:
        metadata.update(pid=os.getpid(), start_identity="live-worker")
    store.update_background(record.id, **metadata)
    with (
        patch(
            "meow.infrastructure.background._process_identity",
            return_value="live-worker",
        ),
        patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run,
    ):
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert store.load(record.id).phase == phase
    assert "worker" in capsys.readouterr().err.lower()


def test_branch_mismatch_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(record.id, "interrupted_mutation", branch="other")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) != 0
    run.assert_not_awaited()
    assert "branch" in capsys.readouterr().err.lower()


def test_repair_refuses_starting_worker_without_rewriting_journal(tmp_path):
    store, record = _record(tmp_path)
    store.update_background(record.id, launch_started_at=time.time())
    before = store.load(record.id)
    assert asyncio.run(resume(tmp_path, record.id, repair=True)) == 1
    assert store.load(record.id) == before


def test_mutating_interruption_resumes_with_review_first(tmp_path):
    _, record = _record(tmp_path)
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "review"


def test_planned_run_resumes_at_generate(tmp_path):
    store, record = _record(tmp_path)
    store.transition(record.id, "planned")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "generate"


def test_integrated_tasks_resume_at_review(tmp_path):
    store, record = _record(tmp_path)
    store.transition(record.id, "tasks_integrated")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "review"


def test_partial_tasks_resume_and_replay_unfinished_slices(tmp_path):
    store, record = _record(tmp_path)
    store.transition(record.id, "tasks_running")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "generate"
    assert store.load(record.id).phase == "resuming"


def test_lint_fix_checkpoint_does_not_start_feature_agent(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    record = RunStore(tmp_path).create(
        source="lint-fix",
        request="",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "lint-fix" in capsys.readouterr().err


def test_missing_worktree_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(
        record.id, "interrupted_mutation", worktree=str(tmp_path / "missing")
    )
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "worktree" in capsys.readouterr().err.lower()


def test_replaced_worktree_from_other_repo_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    replacement = tmp_path.parent / (tmp_path.name + "-replacement")
    replacement.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev", str(replacement)], check=True)
    store.transition(record.id, "interrupted_mutation", worktree=str(replacement))
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "worktree" in capsys.readouterr().err.lower()


def test_changed_plan_refuses_before_agent(tmp_path, capsys):
    import hashlib

    store, record = _record(tmp_path)
    store.transition(
        record.id,
        "interrupted_mutation",
        plan_fingerprint=hashlib.sha256(b"plan").hexdigest(),
    )
    (tmp_path / "plan.md").write_text("changed", encoding="utf-8")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "plan changed" in capsys.readouterr().err.lower()


def test_changed_config_refuses_before_agent(tmp_path, capsys):
    import hashlib

    store, record = _record(tmp_path)
    original = (tmp_path / ".meow" / "config.toml").read_bytes()
    store.transition(
        record.id,
        "interrupted_mutation",
        config_fingerprint=hashlib.sha256(original).hexdigest(),
    )
    (tmp_path / ".meow" / "config.toml").write_text("changed", encoding="utf-8")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "configuration changed" in capsys.readouterr().err.lower()


def test_validate_reads_repository_config_when_worktree_is_linked(tmp_path):
    repo = tmp_path / "repo"
    active = tmp_path / "worktree"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "feature",
            str(active),
            "dev",
        ],
        check=True,
    )
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.toml").write_text("max_rounds = 1\n", encoding="utf-8")

    with patch("meow.project.config.user_config_path", return_value=tmp_path / "none"):
        store = RunStore(repo)
        record = store.create(
            source="prompt",
            request="x",
            repo=repo,
            worktree=active,
            branch="feature",
        )
        store.transition(
            record.id,
            "interrupted_mutation",
            config_fingerprint=config_fingerprint(repo),
        )
        problem = _validate(store.load(record.id), repo)

    assert problem is None


def test_missing_session_reference_refuses_resume(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(record.id, "generator_started")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "missing claude session" in capsys.readouterr().err.lower()


def test_repair_rebaselines_a_changed_plan(tmp_path, capsys):
    store, record = _record(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("one", encoding="utf-8")
    store.transition(
        record.id, "planned", plan_file=str(plan), plan_fingerprint="stale"
    )
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
        run.assert_not_awaited()
        assert (
            asyncio.run(resume(tmp_path, record.id, continue_run=True, repair=True))
            == 0
        )
    assert "re-baselined" in capsys.readouterr().out


def _onboarded_worktree(tmp_path):
    """A linked worktree holding the only config (auto-onboarded, not merged)."""
    repo = tmp_path / "repo"
    active = tmp_path / "worktree"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)
    subprocess.run([*git, "config", "user.name", "Test"], check=True)
    subprocess.run([*git, "config", "user.email", "test@example.com"], check=True)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run([*git, "add", "file.txt"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    subprocess.run(
        [*git, "worktree", "add", "-q", "-b", "feature", str(active)], check=True
    )
    (active / ".meow").mkdir()
    (active / ".meow" / "config.toml").write_text("max_rounds = 1\n", encoding="utf-8")
    store = RunStore(repo)
    record = store.create(
        source="prompt", request="x", repo=repo, worktree=active, branch="feature"
    )
    store.transition(
        record.id,
        "interrupted_mutation",
        config_fingerprint=config_fingerprint(active),
    )
    return repo, active, store, record


def test_validate_accepts_config_that_only_the_worktree_holds(tmp_path):
    with patch("meow.project.config.user_config_path", return_value=tmp_path / "none"):
        repo, _, store, record = _onboarded_worktree(tmp_path)
        assert _validate(store.load(record.id), repo) is None


def test_resume_does_not_report_a_missing_config_for_an_onboarded_worktree(
    tmp_path, capsys
):
    with (
        patch("meow.project.config.user_config_path", return_value=tmp_path / "none"),
        patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run,
    ):
        repo, _, _, record = _onboarded_worktree(tmp_path)
        assert asyncio.run(resume(repo, record.id, continue_run=True)) == 0
    run.assert_awaited_once()
    assert "configuration" not in capsys.readouterr().err.lower()


def test_resume_accepts_parked(tmp_path):
    store, record = _record(tmp_path)
    store.transition(record.id, "planned")
    parked = store.park(record.id, RuntimeError("boom"), "transient", "error")
    assert parked.phase == "parked"
    assert _validate(parked, tmp_path) is None
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    # Resume point comes from the phase the run was parked in, not "parked".
    assert run.await_args.kwargs["resume_at"] == "generate"
    current = store.load(record.id)
    assert current.phase == "resuming"
    assert current.park["phase_before"] == "planned"
    assert "next_command" not in current.results


def test_supervisor_relaunch_may_resume_its_own_run(tmp_path, monkeypatch):
    store, record = _record(tmp_path)
    store.park(record.id, RuntimeError("boom"), "crashed", "driver_died")
    store.update_background(record.id, nonce="n", pid=os.getpid())
    monkeypatch.setenv("MEOW_SUPERVISOR_NONCE", "n")
    with (
        patch(
            "meow.infrastructure.background._process_identity",
            return_value="supervisor",
        ),
        patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run,
    ):
        store.update_background(record.id, start_identity="supervisor")
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    run.assert_awaited_once()
    # The resume owns the run before it looks active again.
    assert store.load(record.id).driver["pid"] == os.getpid()


def _sprint_settling(store, phases):
    async def run_sprint(*_args, **kwargs):  # ruff: ignore[unused-async]
        store.transition(kwargs["run_id"], phases[kwargs["run_id"]])

    return run_sprint


def test_resume_parked_continues_each_run_and_prints_json(tmp_path, capsys):
    store, first = _record(tmp_path)
    second = store.create(
        source="prompt", request="y", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    queue_run = store.create(
        source="queue", request="q", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    for record in (first, second, queue_run):
        store.park(record.id, RuntimeError("boom"), "transient", "error")
    queue = QueueStore(tmp_path)
    item = queue.enqueue("q")
    queue.update(item.id, status="parked", run_id=queue_run.id)
    phases = {first.id: "complete", second.id: "failed"}
    with patch(
        "meow.cli.resume_cli.run_sprint", side_effect=_sprint_settling(store, phases)
    ):
        code = asyncio.run(resume_parked(tmp_path))

    out = capsys.readouterr().out
    summary = json.loads(out)
    assert code == 1  # one run is blocked
    assert {entry["run_id"]: entry["phase"] for entry in summary["resumed"]} == {
        first.id: "complete",
        second.id: "failed",
    }
    # The requeued item reruns from scratch: its parked record is settled, so
    # later sweeps no longer report it.
    assert summary["skipped"] == []
    assert summary["requeued"] == [item.id]
    assert queue.pending()[0].id == item.id
    superseded = store.load(queue_run.id)
    assert (superseded.phase, superseded.results["superseded_by"]) == (
        "cancelled",
        item.id,
    )
    assert store.load(first.id).park["manager_resumes"] == 1


def test_resume_parked_skips_supervised_and_exhausted_runs(tmp_path, capsys):
    store, supervised = _record(tmp_path)
    store.park(supervised.id, "boom", "transient", "error")
    store.update_background(
        supervised.id,
        pid=os.getpid(),
        start_identity=_process_identity(os.getpid()),
    )
    spent = store.create(
        source="prompt", request="y", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.park(spent.id, "boom", "transient", "error")
    current = store.load(spent.id)
    store.transition(spent.id, "parked", park={**current.park, "manager_resumes": 6})
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        # An exhausted budget means stop and escalate, even beside a run its
        # supervisor will still retry.
        assert asyncio.run(resume_parked(tmp_path)) == EXIT_BLOCKED
    run.assert_not_awaited()
    reasons = {
        entry["run_id"]: entry["reason"]
        for entry in json.loads(capsys.readouterr().out)["skipped"]
    }
    assert reasons == {
        supervised.id: "its supervisor is still running",
        spent.id: "managed_resumes reached",
    }


def _parked_prompt_run(store, tmp_path, request="y"):
    record = store.create(
        source="prompt", request=request, repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.park(record.id, "boom", "transient", "error")
    return record


def test_resume_parked_reloads_runs_settled_meanwhile(tmp_path, capsys):
    store, first = _record(tmp_path)
    store.park(first.id, "boom", "transient", "error")
    second = _parked_prompt_run(store, tmp_path)

    async def run_sprint(*_args, **kwargs):  # ruff: ignore[unused-async]
        # While the first run resumes (hours, really), another process
        # continues the second one.
        store.transition(kwargs["run_id"], "complete")
        store.transition(second.id, "resuming")

    with patch("meow.cli.resume_cli.run_sprint", side_effect=run_sprint) as run:
        assert asyncio.run(resume_parked(tmp_path)) == 0
    assert run.await_count == 1
    summary = json.loads(capsys.readouterr().out)
    assert [entry["run_id"] for entry in summary["resumed"]] == [first.id]
    assert summary["skipped"] == []
    assert store.load(second.id).park["manager_resumes"] == 0  # not counted


def test_resume_parked_budget_exhausted_is_reported_not_retried(tmp_path, capsys):
    store, spent = _record(tmp_path)
    store.park(spent.id, "boom", "transient", "error")
    current = store.load(spent.id)
    store.transition(spent.id, "parked", park={**current.park, "manager_resumes": 6})
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        # Not 75: a later sweep could not do anything more; escalate.
        assert asyncio.run(resume_parked(tmp_path)) == EXIT_BLOCKED
    run.assert_not_awaited()
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped"] == [
        {
            "run_id": spent.id,
            "reason": "managed_resumes reached",
            "retryable": False,
            "exhausted": True,
        }
    ]
    assert summary["stop"] == "no_progress"


def test_resume_parked_non_resumable_skip_is_not_a_stop(tmp_path, capsys):
    store = RunStore(tmp_path)
    record = store.create(
        source="queue", request="q", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.park(record.id, "boom", "transient", "error")
    assert asyncio.run(resume_parked(tmp_path)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped"][0]["exhausted"] is False
    assert "stop" not in summary


def test_resume_parked_requeue_counts_and_respects_item_budget(tmp_path, capsys):
    queue = QueueStore(tmp_path)
    fresh = queue.enqueue("fresh")
    worn = queue.enqueue("worn")
    queue.update(fresh.id, status="parked", attempts=1)
    queue.update(worn.id, status="parked", attempts=6)  # managed_resumes = 6

    # The worn item exhausted its budget: stop and escalate.
    assert asyncio.run(resume_parked(tmp_path)) == EXIT_BLOCKED

    summary = json.loads(capsys.readouterr().out)
    assert summary["requeued"] == [fresh.id]
    assert summary["not_requeued"] == [worn.id]
    statuses = {item.id: item.status for item in queue.list()}
    assert statuses == {fresh.id: "pending", worn.id: "parked"}


def test_resume_refuses_while_another_live_process_drives(
    tmp_path, monkeypatch, capsys
):
    store, record = _record(tmp_path)
    store.park(record.id, "boom", "transient", "error")
    current = store.load(record.id)
    current.driver = {
        "pid": os.getppid(),
        "start_identity": _process_identity(os.getppid()),
    }
    store._write(current)
    store.update_background(record.id, nonce="n")
    monkeypatch.setenv("MEOW_SUPERVISOR_NONCE", "n")  # bypasses the worker only
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert store.load(record.id).phase == "parked"
    err = capsys.readouterr().err
    assert f"PID {os.getppid()}" in err
    assert "background worker" not in err


def test_resume_parked_requeue_only_is_tempfail(tmp_path, capsys):
    queue = QueueStore(tmp_path)
    item = queue.enqueue("fresh")
    queue.update(item.id, status="parked", attempts=1)
    assert asyncio.run(resume_parked(tmp_path)) == EXIT_TEMPFAIL
    assert json.loads(capsys.readouterr().out)["requeued"] == [item.id]


def test_resume_parked_rejects_a_run_id(tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["resume", "abc", "--parked", "--work-dir", str(tmp_path)])
    assert exit_info.value.code == 2  # ruff: ignore[magic-value-comparison]


def _running_item(tmp_path):
    """A queue item whose worker died: `running`, run record parked by reconcile."""
    store = _record(tmp_path)[0]
    run = store.create(
        source="queue", request="q", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.park(run.id, "driver died", "crashed", "driver_died")
    queue = QueueStore(tmp_path)
    item = queue.enqueue("q")
    queue.claim(item.id)
    queue.update(item.id, run_id=run.id)
    return store, queue, item, run


def test_resume_parked_requeues_an_item_orphaned_by_a_dead_queue_worker(
    tmp_path, capsys
):
    """The crash used to be found only by restarting `meow queue`: an unattended
    sweep reported nothing to do while the item stayed `running` forever."""
    store, queue, item, run = _running_item(tmp_path)
    assert asyncio.run(resume_parked(tmp_path)) == EXIT_TEMPFAIL
    summary = json.loads(capsys.readouterr().out)
    assert summary["requeued"] == [item.id]
    assert [pending.id for pending in queue.pending()] == [item.id]
    assert store.load(run.id).results["superseded_by"] == item.id
    assert not (queue.directory / "worker.lock").exists()


def test_resume_parked_leaves_a_live_queue_workers_item_alone(tmp_path, capsys):
    store, queue, _item, run = _running_item(tmp_path)
    lock = QueueWorkerLock(queue.directory)
    assert lock.acquire()  # this process is the live worker
    try:
        asyncio.run(resume_parked(tmp_path))
    finally:
        lock.release()
    summary = json.loads(capsys.readouterr().out)
    assert summary["requeued"] == []
    assert [entry.status for entry in queue.list()] == ["running"]
    assert store.load(run.id).phase == "parked"


def _unprepared_run(tmp_path, *, argv):
    """A background run that parked before its worktree and branch existed."""
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="x",
        repo=tmp_path,
        worktree=tmp_path,
        branch="preparing",
    )
    store.transition(record.id, "preparing")
    store.update_background(
        record.id, argv=argv, pid=os.getpid() + 1, start_identity="gone"
    )
    store.park(record.id, "connection reset", "transient", "error")
    return store, record


def test_resume_restarts_a_run_parked_before_its_worktree_existed(tmp_path, capsys):
    """Validation can only fail for such a run ("Branch mismatch: saved
    preparing"), so every managed resume of it was refused and the manager
    needed a human. It starts its saved command again under the same record."""
    argv = ["run", "x", "--background", "--work-dir", str(tmp_path)]
    store, record = _unprepared_run(tmp_path, argv=argv)
    with patch(
        "meow.execution.sprint_runner.run_sprint", new_callable=AsyncMock
    ) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    run.assert_awaited_once()
    assert run.await_args.kwargs["run_id"] == record.id
    assert run.await_args.kwargs["use_worktree"] is True
    assert "before its worktree was prepared" in capsys.readouterr().out
    assert store.load(record.id).phase == "resuming"


def test_resume_without_a_saved_command_still_refuses_an_unprepared_run(tmp_path):
    store, record = _unprepared_run(tmp_path, argv=[])
    store.update_background(record.id, argv=None)
    with patch(
        "meow.execution.sprint_runner.run_sprint", new_callable=AsyncMock
    ) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()


def _parked(tmp_path, **patch):
    store, record = _record(tmp_path)
    store.park(record.id, "boom", "transient", "error")
    if patch:
        store.transition(record.id, "parked", **patch)
    return store, record


def test_continue_counts_a_manager_resume(tmp_path):
    """`meow resume ID --continue` (the next_command) spends the same budget
    as the sweep, once."""
    store, record = _parked(tmp_path)
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock):
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert store.load(record.id).park["manager_resumes"] == 1


def test_supervisor_relaunch_does_not_spend_manager_budget(tmp_path, monkeypatch):
    store, record = _parked(tmp_path)
    store.update_background(record.id, nonce="n", pid=os.getpid())
    monkeypatch.setenv("MEOW_SUPERVISOR_NONCE", "n")
    with (
        patch(
            "meow.infrastructure.background._process_identity",
            return_value="supervisor",
        ),
        patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock),
    ):
        store.update_background(record.id, start_identity="supervisor")
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert store.load(record.id).park["manager_resumes"] == 0


def test_continue_refuses_a_spent_budget_with_a_contract_line(tmp_path, capsys):
    store, record = _parked(tmp_path)
    spent = run_config(tmp_path)["managed_resumes"]
    park = {**store.load(record.id).park, "manager_resumes": spent}
    store.transition(record.id, "parked", park=park)
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    err = capsys.readouterr().err
    assert "MEOW_BLOCKED" in err
    assert "budget_exhausted" in err
    assert store.load(record.id).park["manager_resumes"] == spent


def test_sweep_counts_each_resume_once(tmp_path, capsys):
    store, record = _parked(tmp_path)
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock):
        asyncio.run(resume_parked(tmp_path))
    assert store.load(record.id).park["manager_resumes"] == 1


def test_permanent_refusal_settles_a_parked_run_blocked(tmp_path, capsys):
    """A cron tick re-running next_command must not loop on a worktree that
    will never come back: the run ends failed/invalid_run_state."""
    store, record = _parked(tmp_path, worktree=str(tmp_path / "missing"))
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    current = store.load(record.id)
    assert current.phase == "failed"
    assert current.results["failure"]["cause"] == "invalid_run_state"
    assert "Worktree missing" in current.last_failure
    err = capsys.readouterr().err
    assert "MEOW_BLOCKED" in err
    assert "invalid_run_state" in err


def test_permanent_refusal_does_not_settle_a_run_nobody_parked(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(record.id, "interrupted_mutation", branch="other")
    assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    assert store.load(record.id).phase == "interrupted_mutation"
    assert "MEOW_BLOCKED" in capsys.readouterr().err


def test_refused_sweep_spends_no_budget(tmp_path, capsys):
    store, record = _parked(tmp_path, worktree=str(tmp_path / "missing"))
    asyncio.run(resume_parked(tmp_path))
    assert store.load(record.id).park["manager_resumes"] == 0


def test_claim_refuses_a_run_that_changed_since_it_was_read(tmp_path, capsys):
    store, record = _parked(tmp_path)
    from meow.execution.run_state import RunStateError

    store.transition(record.id, "resuming")  # another process got there first
    with pytest.raises(RunStateError):
        store.claim_resume(record.id, phase="parked")


def test_claim_refuses_a_run_a_live_process_took_over(tmp_path):
    from meow.execution.run_state import RunStateError

    store, record = _parked(tmp_path)
    store.transition(
        record.id,
        "parked",
        driver={"pid": os.getppid(), "start_identity": _process_identity(os.getppid())},
    )
    with pytest.raises(RunStateError):
        store.claim_resume(record.id, phase="parked")


def test_sweep_leaves_orchestrator_child_runs_to_the_tick(tmp_path, capsys):
    """The tick already sent them to triage; a second driver would share
    their worktree."""
    store, _ = _record(tmp_path)
    child = store.create(
        source="orchestrator",
        request="t",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.park(child.id, "boom", "transient", "error")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        asyncio.run(resume_parked(tmp_path))
    run.assert_not_awaited()
    summary = json.loads(capsys.readouterr().out)
    assert child.id not in json.dumps(summary)
    assert store.load(child.id).phase == "parked"

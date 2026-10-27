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
from meow.execution.queue_state import QueueStore
from meow.execution.run_state import RunStore
from meow.infrastructure.checks import config_fingerprint
from meow.infrastructure.supervisor import EXIT_TEMPFAIL


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
    assert summary["skipped"] == [
        {"run_id": queue_run.id, "reason": "queue runs are rerun, not resumed"}
    ]
    assert summary["requeued"] == [item.id]
    assert queue.pending()[0].id == item.id
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
        assert asyncio.run(resume_parked(tmp_path)) == EXIT_TEMPFAIL
    run.assert_not_awaited()
    reasons = {
        entry["run_id"]: entry["reason"]
        for entry in json.loads(capsys.readouterr().out)["skipped"]
    }
    assert reasons == {
        supervised.id: "its supervisor is still running",
        spent.id: "managed_resumes reached",
    }


def test_resume_parked_rejects_a_run_id(tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["resume", "abc", "--parked", "--work-dir", str(tmp_path)])
    assert exit_info.value.code == 2  # ruff: ignore[magic-value-comparison]

"""Cleanup retains the result and only discards integrated, clean workers."""

import pytest

from meow.execution.run_state import RunStore
from tests.execution.test_delivery import git, repository

RESULT_AND_MAIN = 2


@pytest.fixture
def completed_run(tmp_path):
    repo, _ = repository(tmp_path)
    active = repo / ".worktrees" / "csv-export"
    git(repo, "worktree", "add", "--detach", str(active))
    store = RunStore(repo)
    record = store.create(
        source="prompt",
        request="Add CSV export",
        repo=repo,
        worktree=active,
        branch="detached",
    )
    worker = repo / ".worktrees" / f"{record.id}-api"
    git(repo, "worktree", "add", "--detach", str(worker))
    (worker / "api.txt").write_text("API", encoding="utf-8")
    git(worker, "add", "api.txt")
    git(worker, "commit", "-m", "task")
    commit = git(worker, "rev-parse", "HEAD")
    git(active, "cherry-pick", commit)
    store.transition(
        record.id,
        "complete",
        branch="detached",
        results={
            "feature_name": "csv-export",
            "tasks": {
                "api": {
                    "status": "complete",
                    "worktree": str(worker),
                    "commit": commit,
                },
            },
        },
    )
    return repo, store, record.id, active, worker


def cleanup(completed_run):
    from meow.execution.cleanup import cleanup_run

    repo, _, run_id, _, _ = completed_run
    return cleanup_run(repo, run_id)


def test_keeps_named_result_and_removes_worker_idempotently(completed_run):
    repo, store, run_id, active, worker = completed_run
    report = cleanup(completed_run)
    assert not worker.exists()
    assert active.is_dir()
    assert git(active, "branch", "--show-current") == "meow/csv-export"
    assert store.load(run_id).branch == "meow/csv-export"
    assert git(active, "show", "HEAD:api.txt") == "API"
    assert report["removed"] == [str(worker.resolve())]
    assert report["kept"] == []
    assert cleanup(completed_run)["removed"] == []
    listed = git(repo, "worktree", "list", "--porcelain")
    assert listed.count("worktree ") == RESULT_AND_MAIN


@pytest.mark.parametrize(
    "change", ["dirty", "extra-commit", "ignored", "branch", "locked"]
)
def test_preserves_worker_with_new_work(completed_run, change):
    _, _, _, _, worker = completed_run
    if change == "branch":
        git(worker, "switch", "-c", "my-work")
    elif change == "locked":
        git(worker, "worktree", "lock", str(worker))
    else:
        (worker / "scratch.txt").write_text("keep", encoding="utf-8")
        if change == "extra-commit":
            git(worker, "add", "scratch.txt")
            git(worker, "commit", "-m", "unintegrated")
        elif change == "ignored":
            git(worker, "config", "core.excludesFile", str(worker / "scratch.txt"))
            (worker / "scratch.txt").write_text("scratch.txt\n", encoding="utf-8")
    assert cleanup(completed_run)["kept"]
    assert worker.is_dir()


def test_does_not_cleanup_incomplete_run(completed_run):
    _, store, run_id, _, worker = completed_run
    store.transition(run_id, "failed")
    assert cleanup(completed_run)["kept"]
    assert worker.is_dir()


def test_branch_name_collision_does_not_overwrite(completed_run):
    repo, _, _, active, _ = completed_run
    git(repo, "branch", "meow/csv-export", "HEAD")
    before = git(repo, "rev-parse", "meow/csv-export")
    cleanup(completed_run)
    assert git(repo, "rev-parse", "meow/csv-export") == before
    assert git(active, "branch", "--show-current") == "meow/csv-export-2"


def test_branch_namespace_collision_uses_suffix(completed_run):
    repo, _, _, active, _ = completed_run
    git(repo, "branch", "meow/csv-export/topic")
    cleanup(completed_run)
    assert git(active, "branch", "--show-current") == "meow/csv-export-2"


def test_owned_task_plan_does_not_prevent_cleanup(completed_run):
    repo, _, _, _, worker = completed_run
    git(repo, "config", "core.excludesFile", str(repo / ".gitignore"))
    with (repo / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write(".meow/\n")
    plans = worker / ".meow" / "tasks"
    plans.mkdir(parents=True)
    (plans / "api.md").write_text("assigned task", encoding="utf-8")
    assert cleanup(completed_run)["kept"] == []
    assert not worker.exists()


def test_unintegrated_original_commit_is_preserved(completed_run):
    _, _, _, active, worker = completed_run
    git(active, "reset", "--hard", "HEAD^")
    assert cleanup(completed_run)["kept"]
    assert worker.is_dir()


def test_archives_ignored_meow_evidence_before_removal(completed_run):
    repo, _, _, _, worker = completed_run
    runtime = worker / ".meow"
    runtime.mkdir()
    (runtime / "notes.md").write_text("keep evidence", encoding="utf-8")
    git(repo, "config", "core.excludesFile", str(repo / ".gitignore"))
    with (repo / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write(".meow/\n")
    assert cleanup(completed_run)["kept"] == []
    assert not worker.exists()
    archives = list((repo / ".meow" / "cleanup").glob("*/runtime/notes.md"))
    assert [path.read_text() for path in archives] == ["keep evidence"]


def test_does_not_discard_earlier_worker_commits(completed_run):
    _, store, run_id, active, worker = completed_run
    git(active, "reset", "--hard", "HEAD^")
    (worker / "second.txt").write_text("second", encoding="utf-8")
    git(worker, "add", "second.txt")
    git(worker, "commit", "-m", "second")
    commit = git(worker, "rev-parse", "HEAD")
    git(active, "cherry-pick", commit)
    tasks = store.load(run_id).results["tasks"]
    tasks["api"]["commit"] = commit
    store.update_results(run_id, tasks=tasks)
    assert cleanup(completed_run)["kept"]
    assert worker.is_dir()


def test_cleanup_cli_defaults_to_saved_runs(completed_run, capsys, monkeypatch):
    from meow.cli.cli import cli_main

    repo, _, _, _, worker = completed_run
    monkeypatch.setattr("sys.argv", ["meow", "cleanup", "--work-dir", str(repo)])
    cli_main()
    assert not worker.exists()
    assert "csv-export" in capsys.readouterr().out

"""Resume is inspect-first and validates saved identity before mutation."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

from meow.cli.resume_cli import _validate, resume
from meow.execution.run_state import RunStore
from meow.infrastructure.checks import config_fingerprint


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


def test_branch_mismatch_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(record.id, "interrupted_mutation", branch="other")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) != 0
    run.assert_not_awaited()
    assert "branch" in capsys.readouterr().err.lower()


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


def test_partial_tasks_preserve_worktrees_and_refuse_unsafe_replay(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(record.id, "tasks_running")
    with patch("meow.cli.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert store.load(record.id).phase == "tasks_running"
    error = capsys.readouterr().err.lower()
    assert "task worktrees" in error
    assert "review" in error


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

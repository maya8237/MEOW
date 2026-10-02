"""Resume is inspect-first and validates saved identity before mutation."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

from meow.resume_cli import resume
from meow.run_state import RunStore


def _record(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".harness.toml").write_text(
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
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id)) == 0
    run.assert_not_awaited()
    assert "interrupted_mutation" in capsys.readouterr().out


def test_branch_mismatch_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(record.id, "interrupted_mutation", branch="other")
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) != 0
    run.assert_not_awaited()
    assert "branch" in capsys.readouterr().err.lower()


def test_mutating_interruption_resumes_with_review_first(tmp_path):
    _, record = _record(tmp_path)
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "review"


def test_planned_run_resumes_at_generate(tmp_path):
    store, record = _record(tmp_path)
    store.transition(record.id, "planned")
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "generate"


def test_lint_fix_checkpoint_does_not_start_feature_agent(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    record = RunStore(tmp_path).create(
        source="lint-fix",
        request="",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "lint-fix" in capsys.readouterr().err


def test_missing_worktree_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    store.transition(
        record.id, "interrupted_mutation", worktree=str(tmp_path / "missing")
    )
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "worktree" in capsys.readouterr().err.lower()


def test_replaced_worktree_from_other_repo_refuses_before_agent(tmp_path, capsys):
    store, record = _record(tmp_path)
    replacement = tmp_path.parent / (tmp_path.name + "-replacement")
    replacement.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev", str(replacement)], check=True)
    store.transition(record.id, "interrupted_mutation", worktree=str(replacement))
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
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
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "plan changed" in capsys.readouterr().err.lower()


def test_changed_config_refuses_before_agent(tmp_path, capsys):
    import hashlib

    store, record = _record(tmp_path)
    original = (tmp_path / ".harness.toml").read_bytes()
    store.transition(
        record.id,
        "interrupted_mutation",
        config_fingerprint=hashlib.sha256(original).hexdigest(),
    )
    (tmp_path / ".harness.toml").write_text("changed", encoding="utf-8")
    with patch("meow.resume_cli.run_sprint", new_callable=AsyncMock) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "configuration changed" in capsys.readouterr().err.lower()

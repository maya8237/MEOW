"""Task checks must pass before their commits can be integrated."""

import asyncio
import os
import sys

import pytest

from meow.tasks.model import TaskSpec
from meow.tasks.runner import _verification_argv, verify_task


def test_declared_task_verification_records_success(tmp_path):
    spec = TaskSpec("api", (), ("src/api",), (f'{sys.executable} -c "print(123)"',))
    evidence = asyncio.run(verify_task(spec, tmp_path))
    assert evidence[0]["exit_code"] == 0
    assert "123" in evidence[0]["output"]


def test_failed_task_verification_blocks_integration(tmp_path):
    spec = TaskSpec("api", (), ("src/api",), (f'{sys.executable} -c "exit(7)"',))
    with pytest.raises(RuntimeError, match="verification failed"):
        asyncio.run(verify_task(spec, tmp_path))


def test_failed_pipeline_stage_blocks_task_verification(tmp_path):
    spec = TaskSpec(
        "api",
        (),
        ("src/api",),
        (f'{sys.executable} -c "exit(7)" | {sys.executable} -c "print(123)"',),
    )
    with pytest.raises(RuntimeError, match="verification"):
        asyncio.run(verify_task(spec, tmp_path))


def test_quoted_pipe_is_valid_task_verification(tmp_path):
    spec = TaskSpec("api", (), ("src/api",), (f"{sys.executable} -c \"print('a|b')\"",))
    evidence = asyncio.run(verify_task(spec, tmp_path))
    assert evidence[0]["exit_code"] == 0
    assert "a|b" in evidence[0]["output"]


def test_hash_in_filename_does_not_hide_failed_verification(tmp_path):
    (tmp_path / "check#case.py").write_text("pass\n", encoding="utf-8")
    spec = TaskSpec(
        "api",
        (),
        ("src/api",),
        (f'{sys.executable} check#case.py && {sys.executable} -c "exit(7)"',),
    )
    with pytest.raises(RuntimeError, match="verification"):
        asyncio.run(verify_task(spec, tmp_path))


def test_compound_redirect_is_shell_syntax(tmp_path):
    spec = TaskSpec(
        "api",
        (),
        ("src/api",),
        (f'{sys.executable} -c "import sys; print(sys.argv[1:])" 1>&2',),
    )
    evidence = asyncio.run(verify_task(spec, tmp_path))
    assert evidence[0]["output"].strip() == "[]"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable lookup")
def test_task_lint_keeps_native_executable_name(monkeypatch):
    monkeypatch.setattr(
        "shutil.which", lambda program: r"C:\tools with spaces\ruff.exe"
    )
    assert _verification_argv("ruff check .") == ["ruff", "check", "."]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command shim")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_task_lint_shim_in_path_with_spaces(tmp_path, monkeypatch, exit_code):
    tools = tmp_path / "lint tools"
    tools.mkdir()
    (tools / "task-lint.cmd").write_text(
        f"@echo task lint ran\n@exit /b {exit_code}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    worktree = tmp_path / "task worktree"
    worktree.mkdir()
    spec = TaskSpec("lint", (), ("src",), ("task-lint .",))
    if exit_code:
        with pytest.raises(RuntimeError, match=r"verification failed.*exit 7"):
            asyncio.run(verify_task(spec, worktree))
    else:
        evidence = asyncio.run(verify_task(spec, worktree))
        assert evidence[0]["exit_code"] == 0
        assert "task lint ran" in evidence[0]["output"]


def test_resume_retains_completed_worker_metadata_for_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from meow.execution.run_state import RunStore
    from meow.tasks.model import TaskGraph
    from meow.tasks.runner import run_parallel_plan

    store = RunStore(tmp_path)
    run = store.create(
        source="prompt", request="work", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    saved = {
        "status": "complete",
        "commit": "a" * 40,
        "worktree": str(tmp_path / ".worktrees" / f"{run.id}-api"),
        "note": "Verification only; no file changes.",
    }
    store.update_results(run.id, tasks={"api": saved})

    async def execute(*args, **kwargs):  # ruff: ignore[unused-async]
        assert kwargs["completed"] == {"api"}
        return SimpleNamespace(passed=True, revision="a" * 40)

    monkeypatch.setattr("meow.tasks.runner.execute_in_worktrees", execute)
    sprint = SimpleNamespace(config={}, active_working_dir=lambda: tmp_path)
    graph = TaskGraph((TaskSpec("api", (), ("api.txt",), ()),))
    assert asyncio.run(
        run_parallel_plan(sprint, graph, tmp_path / "plan.md", store, run.id)
    )
    task = store.load(run.id).results["tasks"]["api"]
    assert task["commit"] == saved["commit"]
    assert task["worktree"] == saved["worktree"]
    assert task["note"] == saved["note"]

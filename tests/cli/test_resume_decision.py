"""A product-decision checkpoint can be clarified without losing its checkout."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli.cli import cli_main
from meow.cli.resume_cli import resume
from meow.cli.status_cli import status
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint

REQUEST = "Choose between deleting accounts or retaining them forever"
CLARIFIED = "Retain accounts and their history forever"


def paused_run(tmp_path, *, linked=False):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    directory = tmp_path / ".meow"
    (directory / "workflows").mkdir(parents=True)
    (directory / "config.toml").write_text(
        '[workflow]\ndefault="decision"\n', encoding="utf-8"
    )
    (directory / "workflows" / "decision.toml").write_text(
        'description="Plan a clarified request"\n'
        '[[steps]]\nuses="builtin.knowledge"\n'
        '[[steps]]\nuses="builtin.shape"\n'
        '[[steps]]\nuses="builtin.plan"\n',
        encoding="utf-8",
    )
    if linked:
        subprocess.run(["git", "-C", str(tmp_path), "add", ".meow"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "config",
            ],
            check=True,
        )
    with pytest.raises(RuntimeError, match="product decision"):
        asyncio.run(run_sprint(tmp_path, "accounts", REQUEST, use_worktree=linked))
    return RunStore(tmp_path).latest()


def test_status_explains_the_decision_and_how_to_supply_it(tmp_path, capsys):
    record = paused_run(tmp_path)
    assert status(tmp_path, record.id) == 0
    output = capsys.readouterr().out
    assert REQUEST in output
    assert "Choose between" in output
    assert f"meow resume {record.id}" in output
    assert '--continue --request "' in output


@pytest.mark.parametrize("linked", [False, True])
def test_cli_clarification_replans_in_the_saved_run(tmp_path, linked):
    record = paused_run(tmp_path, linked=linked)
    plan = Path(record.worktree) / ".meow" / "plans" / "accounts.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# Retain accounts\n", encoding="utf-8")
    with (
        patch(
            "meow.execution.workflow.builtin_steps.PlannerAgent.run",
            new=AsyncMock(return_value=plan),
        ) as planner,
        pytest.raises(SystemExit) as exit_info,
    ):
        cli_main([
            "resume",
            record.id,
            "--continue",
            "--request",
            CLARIFIED,
            "--work-dir",
            str(tmp_path),
        ])
    assert exit_info.value.code == 0
    assert planner.await_args.args[1] == CLARIFIED
    current = RunStore(tmp_path).load(record.id)
    assert current.phase == "complete"
    assert current.request == CLARIFIED
    assert current.results["original_request"] == REQUEST
    assert current.results["preplan"]["decision"]["mode"] != "needs_user_decision"
    assert current.worktree == record.worktree
    assert current.plan_file == str(plan)
    assert current.last_failure is None
    assert len(RunStore(tmp_path).record_paths()) == 1


@pytest.mark.parametrize("clarification", [None, "", "   ", REQUEST])
def test_unresolved_resume_does_not_change_the_checkpoint(
    tmp_path, clarification, capsys
):
    record = paused_run(tmp_path)
    store = RunStore(tmp_path)
    before = store.load(record.id)
    assert (
        asyncio.run(
            resume(tmp_path, record.id, continue_run=True, request=clarification)
        )
        == 1
    )
    assert store.load(record.id) == before
    assert "--request" in capsys.readouterr().err


def test_clarification_requires_continue(tmp_path, capsys):
    record = paused_run(tmp_path)
    before = RunStore(tmp_path).load(record.id)
    assert asyncio.run(resume(tmp_path, record.id, request=CLARIFIED)) == 1
    assert RunStore(tmp_path).load(record.id) == before
    assert "--continue" in capsys.readouterr().err


@pytest.mark.parametrize("phase", ["planned", "generator_started", "complete"])
def test_other_runs_cannot_replace_their_request(tmp_path, phase, capsys):
    record = paused_run(tmp_path)
    store = RunStore(tmp_path)
    before = store.transition(record.id, phase)
    assert (
        asyncio.run(resume(tmp_path, record.id, continue_run=True, request=CLARIFIED))
        == 1
    )
    assert store.load(record.id) == before
    assert "before planning" in capsys.readouterr().err


def test_clarification_still_validates_saved_identity(tmp_path, capsys):
    record = paused_run(tmp_path)
    store = RunStore(tmp_path)
    before = store.transition(record.id, "needs_user_decision", branch="other")
    assert (
        asyncio.run(resume(tmp_path, record.id, continue_run=True, request=CLARIFIED))
        == 1
    )
    assert store.load(record.id) == before
    assert "branch" in capsys.readouterr().err.lower()


def test_decision_checkpoint_after_generation_cannot_change_scope(tmp_path, capsys):
    record = paused_run(tmp_path)
    store = RunStore(tmp_path)
    store.transition(record.id, "generator_started")
    before = store.transition(record.id, "needs_user_decision")
    assert (
        asyncio.run(resume(tmp_path, record.id, continue_run=True, request=CLARIFIED))
        == 1
    )
    assert store.load(record.id) == before
    assert "before planning" in capsys.readouterr().err

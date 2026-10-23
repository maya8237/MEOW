import asyncio
import subprocess
import sys
from unittest.mock import patch

import pytest

from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.sprint_runner import run_sprint
from meow.execution.workflow.engine import RunState, run_workflow
from meow.infrastructure.checks import Check, code_revision
from meow.infrastructure.review_verification import review_evidence
from meow.project.config import load_config
from meow.project.custom.workflows import expand


@pytest.mark.parametrize("workflow", ["default", "quick", "review-and-fix"])
def test_failed_checks_skip_review_and_passing_checks_are_reused(tmp_path, workflow):  # ruff: ignore[too-many-statements] -- exercise the complete real check loop
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text("", encoding="utf-8")
    source = tmp_path / "source.py"
    source.write_text("fail", encoding="utf-8")
    config = load_config(tmp_path)
    counter = tmp_path.parent / (tmp_path.name + "-checks.txt")
    command = (
        "from pathlib import Path; "
        f"p=Path({str(counter)!r}); "
        "p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
        "assert Path('source.py').read_text() == 'pass', 'fix source.py'"
    )
    config["build"] = [Check("build", "build", sys.executable, ("-c", command))]
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="fix", repo=tmp_path, worktree=tmp_path, branch="main"
    )
    state = RunState(
        Sprint(tmp_path, config, None, None),
        store,
        record.id,
        plan_file=tmp_path / "plan.md",
        checks_dir=tmp_path,
    )
    steps = expand(
        config["customizations"].workflow(workflow), config["customizations"]
    )
    loop = next(step for step in steps if step.kind == "loop")
    final = next(step for step in steps if step.target == "builtin.checks")
    reviewed = []

    async def generate(current):  # ruff: ignore[unused-async]
        assert current.round == 1 or "fix source.py" in current.feedback
        source.write_text("pass" if current.round > 1 else "fail", encoding="utf-8")

    async def review(*args, **kwargs):
        assert not (await review_evidence(tmp_path, config)).blocking_failed
        reviewed.append(source.read_text(encoding="utf-8"))
        (tmp_path / "review.md").write_text("report", encoding="utf-8")
        store.update_results(
            record.id, reviewer="PASS", reviewer_revision=code_revision(tmp_path)
        )
        return "PASS", "SUMMARY: works\nSTATUS: PASS"

    with (
        patch("meow.execution.workflow.builtin_steps.generate", new=generate),
        patch("meow.execution.workflow.builtin_steps._review_round", new=review),
    ):
        asyncio.run(run_workflow(state, (loop, final)))
    assert reviewed == ["pass"]
    assert counter.read_text(encoding="utf-8") == "xx"
    assert all(item.passed for item in state.checks_results)


def test_force_disabled_policy_survives_resume_and_overrides_explicit_test(tmp_path):
    (tmp_path / ".meow/workflows").mkdir(parents=True)
    (tmp_path / ".meow/config.toml").write_text(
        '[[tester.tests]]\ncommand="missing-test-command"\n', encoding="utf-8"
    )
    (tmp_path / ".meow/workflows/empty.toml").write_text(
        'description="Empty"\nsteps=[]\n', encoding="utf-8"
    )
    asyncio.run(
        run_sprint(
            tmp_path,
            None,
            "request",
            use_worktree=False,
            workflow="empty",
            test=True,
            force_disable_tests=True,
        )
    )
    store = RunStore(tmp_path)
    record = store.latest()
    assert record.results["force_disable_tests"] is True
    assert record.results["test"] is False
    assert record.results["skipped_checks"] == ["AI tester", "test-1"]
    asyncio.run(
        run_sprint(
            tmp_path,
            None,
            "request",
            use_worktree=False,
            workflow="empty",
            run_id=record.id,
            test=True,
        )
    )
    assert store.load(record.id).results["test"] is False

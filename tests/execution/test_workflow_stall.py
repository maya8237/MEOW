import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.workflow.engine import (
    LoopExhausted,
    LoopStalled,
    RunState,
    run_workflow,
)
from meow.execution.workflow.stall import StallTracker, failure_signature
from meow.project.config import load_config
from meow.project.custom.workflows import Step
from meow.project.plan_files import plan_review_file

LIMIT = 2
STALL_ROUND = 2


def state_for(tmp_path, *, plan=False, **config_overrides):
    config = load_config(tmp_path)
    config.update(config_overrides)
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="Improve",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    config["_run_journal"] = store, record.id
    plan_file = tmp_path / "plan.md" if plan else None
    if plan:
        plan_file.write_text("Plan", encoding="utf-8")
    return RunState(
        sprint=Sprint(tmp_path, config, None, None),
        store=store,
        run_id=record.id,
        plan_file=plan_file,
        checks_dir=tmp_path,
        feature_name="feature",
        request="Improve",
    )


def gate_loop(*extra, max_rounds=6):
    gate = Step("gate", "audit", target="reviewer", blocks="loop")
    return Step("loop", "rounds", max_rounds=max_rounds, body=(*extra, gate))


def scripted_gate(findings):
    """Stage double writing `findings[round - 1]` (last one repeats) as the verdict."""
    calls = []

    async def run(agent, task, values, pool):  # ruff: ignore[unused-async] -- SDK double
        calls.append(task)
        text = findings[min(len(calls), len(findings)) - 1]
        values["output_file"].write_text(text, encoding="utf-8")

    return calls, patch(
        "meow.execution.workflow.stages.StageAgent.run", side_effect=run
    )


def test_signature_ignores_status_case_and_whitespace():
    first = "SUMMARY: bad\nSTATUS: FAIL\n- FAIL  Criterion 1:   no   guard"
    second = "SUMMARY: other words\nSTATUS: FAIL\n  - FAIL criterion 1: NO guard "
    assert failure_signature([first]) == failure_signature([second])
    assert "status: fail" not in " ".join(failure_signature([first]))


def test_signature_differs_for_new_findings_and_falls_back_to_summary():
    one = "SUMMARY: a\nSTATUS: FAIL\n- FAIL one"
    two = "SUMMARY: a\nSTATUS: FAIL\n- FAIL one\n- FAIL two"
    assert failure_signature([one]) != failure_signature([two])
    no_findings = "SUMMARY: Missing   Thing\nSTATUS: PASS"
    assert failure_signature([no_findings]) == frozenset({"summary: missing thing"})


def test_tracker_counts_consecutive_identical_rounds_only():
    tracker = StallTracker(LIMIT)
    same, other = ["- FAIL a"], ["- FAIL b"]
    assert tracker.check(1, same, None) is None
    assert tracker.check(2, other, True) is None
    assert tracker.check(3, other, True)[0] == "repeated_findings"


def test_tracker_stops_on_a_fix_round_without_changes_and_can_be_disabled():
    assert StallTracker(LIMIT).check(1, ["- FAIL a"], False) is None
    assert StallTracker(LIMIT).check(2, ["- FAIL a"], False)[0] == "no_change"
    assert StallTracker(LIMIT).check(2, ["- FAIL a"], None) is None
    off = StallTracker(0)
    assert off.check(2, ["- FAIL a"], False) is None
    assert off.check(3, ["- FAIL a"], False) is None


def test_identical_findings_stall_the_loop_and_are_journaled(tmp_path):
    state = state_for(tmp_path)
    calls, stage = scripted_gate(["SUMMARY: x\nSTATUS: FAIL\n- FAIL auth is open"])
    with stage, pytest.raises(LoopStalled, match="stalled at round 2") as raised:
        asyncio.run(run_workflow(state, (gate_loop(),)))
    assert len(calls) == STALL_ROUND
    assert isinstance(raised.value, LoopExhausted)
    assert "repeated for 2 consecutive rounds" in str(raised.value)
    stalled = state.store.load(state.run_id).results["loop_stalled"]
    assert stalled == {
        "loop": "rounds",
        "round": STALL_ROUND,
        "reason": "repeated_findings",
    }


def test_changing_findings_keep_the_loop_going(tmp_path):
    state = state_for(tmp_path)
    calls, stage = scripted_gate([
        "SUMMARY: x\nSTATUS: FAIL\n- FAIL one",
        "SUMMARY: x\nSTATUS: FAIL\n- FAIL two",
        "SUMMARY: ok\nSTATUS: PASS",
    ])
    with stage:
        asyncio.run(run_workflow(state, (gate_loop(),)))
    assert len(calls) == len(["one", "two", "ok"])
    assert "loop_stalled" not in state.store.load(state.run_id).results


def test_stall_rounds_zero_runs_to_the_round_limit(tmp_path):
    state = state_for(tmp_path, stall_rounds=0)
    calls, stage = scripted_gate(["SUMMARY: x\nSTATUS: FAIL\n- FAIL same"])
    with stage, pytest.raises(LoopExhausted, match="did not pass after 3") as raised:
        asyncio.run(run_workflow(state, (gate_loop(max_rounds=3),)))
    assert not isinstance(raised.value, LoopStalled)
    assert len(calls) == len(["r1", "r2", "r3"])


def fix_step_with_revisions(revisions):
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock()
    patches = (
        patch(
            "meow.execution.workflow.builtin_steps._generator",
            new=AsyncMock(return_value=generator),
        ),
        patch("meow.execution.workflow.builtin_steps._generate", new=AsyncMock()),
        patch(
            "meow.execution.workflow.builtin_steps.code_revision",
            side_effect=revisions,
        ),
    )
    return Step("uses", "fix", target="builtin.generate", when="round > 1"), patches


def test_fix_round_without_code_change_stalls_even_with_new_findings(tmp_path):
    state = state_for(tmp_path, plan=True)
    fix, patches = fix_step_with_revisions(["rev", "rev"])
    calls, stage = scripted_gate([
        "SUMMARY: x\nSTATUS: FAIL\n- FAIL one",
        "SUMMARY: x\nSTATUS: FAIL\n- FAIL two",
    ])
    with (
        stage,
        patches[0],
        patches[1],
        patches[2],
        pytest.raises(LoopStalled, match="changed no code") as raised,
    ):
        asyncio.run(run_workflow(state, (gate_loop(fix),)))
    assert raised.value.reason == "no_change"
    assert len(calls) == STALL_ROUND
    assert (
        state.store.load(state.run_id).results["loop_stalled"]["reason"] == "no_change"
    )


def test_fix_round_with_code_change_is_not_a_stall(tmp_path):
    state = state_for(tmp_path, plan=True)
    fix, patches = fix_step_with_revisions(["before", "after"])
    calls, stage = scripted_gate([
        "SUMMARY: x\nSTATUS: FAIL\n- FAIL one",
        "SUMMARY: ok\nSTATUS: PASS",
    ])
    with stage, patches[0], patches[1], patches[2]:
        asyncio.run(run_workflow(state, (gate_loop(fix),)))
    assert len(calls) == STALL_ROUND


def test_stall_message_names_review_and_tester_files(tmp_path):
    state = state_for(tmp_path, plan=True)
    state.test = True
    step = Step(
        "loop", "rounds", body=(Step("uses", "review", target="builtin.review"),)
    )
    with (
        patch(
            "meow.execution.orchestrator.ReviewerAgent.review_plan",
            new=AsyncMock(
                return_value=("FAIL", "SUMMARY: x\nSTATUS: FAIL\n- FAIL same")
            ),
        ),
        pytest.raises(LoopStalled) as raised,
    ):
        asyncio.run(run_workflow(state, (step,)))
    message = str(raised.value)
    assert "Sprint 'feature' loop 'rounds' stalled at round 2" in message
    assert str(plan_review_file(state.plan_file)) in message
    assert "tester file" in message

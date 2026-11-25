import asyncio
from unittest.mock import patch

from meow.execution.workflow.engine import run_workflow
from meow.project.custom.workflows import Step
from tests.execution.test_workflow_stall import state_for


def fix_loop():
    fix = Step("uses", "fix", when="round > 1 or feedback", target="builtin.generate")
    gate = Step("gate", "audit", target="reviewer", blocks="loop")
    return Step("loop", "rounds", max_rounds=2, body=(fix, gate))


def run_loop(tmp_path, flags):
    fixes = []

    async def generate(state):  # ruff: ignore[unused-async] -- SDK double
        fixes.append((state.round, state.feedback))

    async def stage(agent, task, values, pool):  # ruff: ignore[unused-async] -- SDK double
        values["output_file"].write_text(
            "SUMMARY: ok\nSTATUS: PASS\n", encoding="utf-8"
        )

    state = state_for(tmp_path)
    state.flags.update(flags)
    with (
        patch("meow.execution.workflow.builtin_steps.generate", side_effect=generate),
        patch("meow.execution.workflow.stages.StageAgent.run", side_effect=stage),
    ):
        asyncio.run(run_workflow(state, (fix_loop(),)))
    return state, fixes


def test_hand_edited_verdict_seeds_the_first_fix_round(tmp_path):
    state, fixes = run_loop(tmp_path, {"resume_feedback": "Verdict edited: login"})
    assert fixes == [(1, "Verdict edited: login")]
    assert "resume_feedback" not in state.flags


def test_no_resume_feedback_skips_the_first_fix_round(tmp_path):
    _, fixes = run_loop(tmp_path, {})
    assert fixes == []


def test_resume_notes_seed_the_first_fix_round(tmp_path):
    state, fixes = run_loop(tmp_path, {"resume_notes": "Notes: use v2"})
    assert fixes == [(1, "Notes: use v2")]
    assert "resume_notes" not in state.flags
    assert state.flags["notes_pending"] is True  # generate consumes it


def test_resume_notes_come_before_edited_verdicts(tmp_path):
    _, fixes = run_loop(
        tmp_path, {"resume_notes": "Notes: use v2", "resume_feedback": "Verdict: x"}
    )
    assert fixes == [(1, "Notes: use v2\n\nVerdict: x")]


def test_generate_names_the_users_notes_once():
    from types import SimpleNamespace

    from meow.execution.workflow.builtin_steps import _generate_instruction

    state = SimpleNamespace(
        feedback="Notes: use v2",
        flags={"notes_pending": True},
        test=False,
        plan_file="plan.md",
    )
    first = _generate_instruction(state)
    assert "paused" in first and "Notes: use v2" in first
    assert "reviewer found issues" not in first
    assert "notes_pending" not in state.flags
    state.feedback = "SUMMARY: bad\nSTATUS: FAIL"
    assert "reviewer found issues" in _generate_instruction(state)

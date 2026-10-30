import asyncio
from unittest.mock import AsyncMock, patch

from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.execution.workflow import builtin_steps
from meow.project.custom.definitions import discover
from meow.project.custom.sources import builtin_sources
from meow.project.custom.workflows import validate
from meow.project.plan_files import stage_file

ROUNDS = 4
PLAN = "\n\n## Test plan\n- page: tested"


def _project(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[workflow]\ndefault="test-fix"', encoding="utf-8"
    )
    return stage_file(tmp_path / ".meow" / "plans", "tester")


def _run(tmp_path, verdicts):
    """Run test-fix with scripted tester verdicts; return (generate calls, error)."""
    report = _project(tmp_path)
    queue = iter(verdicts)
    feedback = []

    async def query(self, agent, prompt, *args):  # ruff: ignore[unused-async] -- SDK double
        verdict = next(queue)
        report.write_text(f"SUMMARY: {verdict}\nSTATUS: {verdict}{PLAN}", "utf-8")

    async def generate(state, step=None):  # ruff: ignore[unused-async] -- SDK double
        feedback.append(state.feedback)

    error = None
    with (
        patch("meow.agents.tester.VerificationAgent.run_definition_query", new=query),
        patch.object(builtin_steps, "generate", generate),
        patch(
            "meow.execution.workflow.builtin_steps.ReviewerAgent.review_prompt",
            new=AsyncMock(return_value=("PASS", "SUMMARY: OK\nSTATUS: PASS")),
        ),
    ):
        try:
            asyncio.run(
                run_sprint(tmp_path, None, "Audit page", use_worktree=False, test=True)
            )
        except Exception as exc:
            error = exc
    return feedback, error


def test_round_one_skips_fix_and_passes_when_test_and_review_pass(tmp_path):
    feedback, error = _run(tmp_path, ["PASS"])
    assert error is None
    assert feedback == []
    assert RunStore(tmp_path).latest().phase == "complete"


def test_tester_fail_triggers_fix_with_report_then_passes(tmp_path):
    feedback, error = _run(tmp_path, ["FAIL", "PASS"])
    assert error is None
    assert len(feedback) == 1
    assert "Tester feedback" in feedback[0]
    assert "FAIL" in feedback[0]


def test_stops_failed_after_four_rounds(tmp_path):
    feedback, error = _run(tmp_path, ["FAIL"] * ROUNDS)
    assert error is not None
    assert len(feedback) == ROUNDS - 1
    assert RunStore(tmp_path).latest().phase != "complete"


def test_review_does_not_run_after_a_tester_failure(tmp_path):
    _project(tmp_path)
    review = AsyncMock(return_value=("PASS", "SUMMARY: OK\nSTATUS: PASS"))
    report = stage_file(tmp_path / ".meow" / "plans", "tester")
    queue = iter(["FAIL", "PASS"])

    async def query(self, agent, prompt, *args):  # ruff: ignore[unused-async] -- SDK double
        verdict = next(queue)
        report.write_text(f"SUMMARY: {verdict}\nSTATUS: {verdict}{PLAN}", "utf-8")

    with (
        patch("meow.agents.tester.VerificationAgent.run_definition_query", new=query),
        patch.object(builtin_steps, "generate", AsyncMock()),
        patch(
            "meow.execution.workflow.builtin_steps.ReviewerAgent.review_prompt",
            new=review,
        ),
    ):
        asyncio.run(run_sprint(tmp_path, None, "Audit page", use_worktree=False))
    assert review.await_count == 1


def test_workflow_is_shipped():
    definitions = discover(builtin_sources())
    assert validate(definitions.workflow("test-fix"), definitions) == []

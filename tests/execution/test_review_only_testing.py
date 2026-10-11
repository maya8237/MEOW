import asyncio
from unittest.mock import AsyncMock, patch

from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.project.plan_files import stage_file


def test_review_only_supports_testing_without_a_plan(tmp_path):
    directory = tmp_path / ".meow"
    directory.mkdir()
    (directory / "config.toml").write_text(
        '[workflow]\ndefault="review-only"', encoding="utf-8"
    )
    report = stage_file(tmp_path / ".meow" / "plans", "tester")

    async def query(self, agent, prompt, *args):  # ruff: ignore[unused-async] -- SDK double
        assert "Audit API" in prompt
        report.write_text("SUMMARY: Verified\nSTATUS: PASS", encoding="utf-8")

    with (
        patch(
            "meow.execution.workflow.builtin_steps.ReviewerAgent.review_prompt",
            new=AsyncMock(return_value=("PASS", "SUMMARY: OK\nSTATUS: PASS")),
        ),
        patch("meow.agents.tester.VerificationAgent.run_definition_query", new=query),
        patch("meow.execution.workflow.builtin_steps.deliver_verified_run") as deliver,
    ):
        asyncio.run(
            run_sprint(tmp_path, None, "Audit API", use_worktree=False, test=True)
        )
    assert RunStore(tmp_path).latest().phase == "complete"
    assert RunStore(tmp_path).latest().results["tester"] == "PASS"
    deliver.assert_not_called()

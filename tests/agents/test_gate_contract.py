import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from meow.agents.base import Agent, ProjectContext
from meow.agents.reviewer import ReviewerAgent
from meow.project.config import load_config
from meow.project.plan_files import plan_review_file


@pytest.mark.parametrize("corrected", ["PASS", "FAIL", None])
@pytest.mark.parametrize(
    "malformed",
    ["## SUMMARY: reviewed\n## STATUS: PASS", "SUMMARY: ok\nSTATUS: **PASS**"],
)
def test_reviewer_corrects_malformed_verdict_once(tmp_path, corrected, malformed):
    plan = tmp_path / "plan.md"
    plan.write_text("Plan", encoding="utf-8")
    report = plan_review_file(plan)
    context = ProjectContext(tmp_path, load_config(tmp_path))
    prompts = []

    async def query(request, options, role):  # ruff: ignore[unused-async]
        prompts.append(request)
        text = malformed
        if len(prompts) > 1 and corrected:
            text = f"SUMMARY: reviewed\nSTATUS: {corrected}"
        report.write_text(text, encoding="utf-8")

    with patch.object(Agent, "run_query", side_effect=query):
        status, verdict = asyncio.run(ReviewerAgent(context).review_plan(plan))
    initial, correction = prompts
    assert "Review " in initial
    assert "Preserve the findings" in correction
    assert status == (corrected or "FAIL")
    if corrected is None:
        assert "missing a plain STATUS: line" in verdict
        assert "STATUS: FAIL" in verdict


def test_reviewer_override_gets_contract_and_missing_verdict_fails(tmp_path):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "reviewer.toml").write_text(
        'description="Review"\ntools=["Read","Write"]\nprompt="Review changes."',
        encoding="utf-8",
    )
    plan = tmp_path / "plan.md"
    plan.write_text("Plan", encoding="utf-8")
    context = ProjectContext(tmp_path, load_config(tmp_path))
    captured = []

    async def query(request, options, role):  # ruff: ignore[unused-async] -- SDK double
        captured.append(options.system_prompt)

    with patch.object(Agent, "run_query", side_effect=query):
        status, verdict = asyncio.run(ReviewerAgent(context).review_plan(plan))
    assert status == "FAIL"
    assert "readable verdict" in verdict
    assert "SUMMARY:" in captured[0]
    assert "STATUS: PASS" in captured[0]
    assert "plan-review.md" in captured[0]


def test_reviewer_without_variants_supports_prompt_review(tmp_path):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "reviewer.toml").write_text(
        'description="Review"\nprompt="My review."', encoding="utf-8"
    )
    context = ProjectContext(tmp_path, load_config(tmp_path))
    with (
        patch.object(Agent, "run_query", new=AsyncMock()),
        patch("meow.agents.reviewer._git_review_context", return_value=("diff", True)),
    ):
        status, _ = asyncio.run(ReviewerAgent(context).review_prompt("request"))
    assert status == "FAIL"

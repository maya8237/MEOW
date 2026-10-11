import asyncio
from unittest.mock import patch

from meow.agents.base import Agent, ProjectContext
from meow.agents.reviewer import ReviewerAgent
from meow.project.config import load_config


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

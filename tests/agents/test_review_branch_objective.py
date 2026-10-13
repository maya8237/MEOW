"""`review_branch` can grade a branch against an objective and exposes its
verdict file."""

import asyncio
from unittest.mock import AsyncMock

from meow.agents.base import ProjectContext
from meow.agents.reviewer import ReviewerAgent
from meow.infrastructure.lint import LintGateEvidence

VERDICT = "SUMMARY: reviewed\nSTATUS: PASS\nconcern: PASS\n"


def review(tmp_path, monkeypatch, **options):
    """Run `review_branch` with a stub model; return (agent, result, system prompt)."""
    context = ProjectContext(
        tmp_path,
        {"models": {}, "lint": [], "docs_dir": ".meow/plans", "lint_timeout": 60},
        use_worktree=True,
    )
    agent = ReviewerAgent(context)
    captured = {}

    async def fake_query(  # ruff: ignore[too-many-arguments, too-many-positional-arguments, unused-async] -- mirrors the real signature
        self, definition, prompt, query_options, role
    ):
        captured["system_prompt"] = query_options.system_prompt
        self.last_review_file.write_text(VERDICT, encoding="utf-8")

    monkeypatch.setattr(ReviewerAgent, "run_definition_query", fake_query)
    monkeypatch.setattr(
        ReviewerAgent, "_lint_evidence", AsyncMock(return_value=LintGateEvidence())
    )
    monkeypatch.setattr("meow.agents.reviewer._branch_diff", lambda *args: "diff")
    result = asyncio.run(agent.review_branch("main", "orch/abc", **options))
    return agent, result, captured["system_prompt"]


def test_objective_reaches_the_reviewer_prompt(tmp_path, monkeypatch):
    _agent, result, system_prompt = review(
        tmp_path, monkeypatch, objective="Add login\n\nTasks:\n- api: endpoint"
    )

    assert result == ("PASS", VERDICT)
    assert "Grade the whole branch against it" in system_prompt
    assert "- api: endpoint" in system_prompt


def test_without_an_objective_the_prompt_has_no_objective_text(tmp_path, monkeypatch):
    _agent, _result, system_prompt = review(tmp_path, monkeypatch)

    assert "Grade the whole branch against it" not in system_prompt


def test_last_review_file_is_the_branch_verdict_file(tmp_path, monkeypatch):
    agent, _result, _prompt = review(tmp_path, monkeypatch)

    assert agent.last_review_file.parent == tmp_path / ".meow" / "plans"
    assert agent.last_review_file.name.startswith("branch.")
    assert agent.last_review_file.name.endswith(".review.md")
    assert agent.last_review_file.read_text(encoding="utf-8") == VERDICT

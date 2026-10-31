import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meow.agents.reviewer import MAX_MEMORY_CHARS, PriorReview, plan_review_query
from meow.execution import orchestrator, round_memory
from meow.execution.sprint import Sprint
from meow.project.config import load_config
from meow.project.plan_files import plan_review_file

FAIL = "SUMMARY: nope\nSTATUS: FAIL\n- FAIL criterion 1"
PASS = "SUMMARY: ok\nSTATUS: PASS"
SECOND_ROUND = 2


def git(repo, *argv):
    subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def sprint(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "plan.md").write_text("Plan", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    return Sprint(tmp_path, load_config(tmp_path), None, None)


def test_query_without_prior_is_unchanged():
    assert "re-review" not in plan_review_query("plan.md", "lint")


def test_query_carries_verdict_reply_and_changes():
    prior = PriorReview(FAIL, "Finding 2 is out of scope.", " code.py | 2 +-", "+x = 2")
    query = plan_review_query("plan.md", "lint", prior)
    assert "re-review" in query
    assert "- FAIL criterion 1" in query
    assert "Finding 2 is out of scope." in query
    assert "code.py | 2 +-" in query
    assert "+x = 2" in query
    assert "since previous review" in query


def test_query_clips_long_memory_and_notes_missing_reply():
    prior = PriorReview(
        "v" * (MAX_MEMORY_CHARS + 5), "", "", "d" * (MAX_MEMORY_CHARS * 2)
    )
    query = plan_review_query("plan.md", "lint", prior)
    assert "previous verdict truncated: 5 more characters omitted" in query
    assert "diff since previous review truncated" in query
    assert "(no reply captured)" in query
    assert "(no file changes)" in query


def test_generate_stores_the_final_reply(sprint):
    generator = MagicMock()
    generator.implement = AsyncMock(return_value="Fixed 1; 2 is out of scope.")
    asyncio.run(orchestrator._generate(generator, sprint, "go", 1))
    assert sprint.config[round_memory.REPLY_KEY] == "Fixed 1; 2 is out of scope."


def test_first_review_has_no_prior_and_second_gets_everything(sprint, tmp_path):
    plan = tmp_path / "plan.md"
    review = AsyncMock(side_effect=[("FAIL", FAIL), ("PASS", PASS)])
    generator = MagicMock()
    generator.implement = AsyncMock(return_value="rebuttal text")
    with patch("meow.execution.orchestrator.ReviewerAgent.review_plan", new=review):
        asyncio.run(orchestrator._review_plan(sprint, plan))
        (tmp_path / "code.py").write_text("x = 2\n", encoding="utf-8")
        asyncio.run(orchestrator._generate(generator, sprint, "fix", SECOND_ROUND))
        asyncio.run(orchestrator._review_plan(sprint, plan))
    assert "prior" not in review.await_args_list[0].kwargs
    prior = review.await_args_list[1].kwargs["prior"]
    assert prior.verdict == FAIL
    assert prior.reply == "rebuttal text"
    assert prior.since == "previous review"
    assert "code.py" in prior.stat
    assert "+x = 2" in prior.diff
    assert round_memory.REPLY_KEY not in sprint.config  # consumed by that review
    assert sprint.config[round_memory.VERDICT_KEY] == PASS


def test_diff_excludes_the_report_files(sprint, tmp_path):
    plan = tmp_path / "plan.md"
    asyncio.run(round_memory.remember(sprint, plan, FAIL))
    plan_review_file(plan).write_text("verdict", encoding="utf-8")
    prior = asyncio.run(round_memory.prior_for(sprint, plan))
    assert plan_review_file(plan).name not in prior.stat


def test_non_repository_falls_back_without_failing(tmp_path):
    sprint = Sprint(tmp_path, load_config(tmp_path), None, None)
    asyncio.run(round_memory.remember(sprint, tmp_path / "plan.md", FAIL))
    assert sprint.config[round_memory.TREE_KEY] is None
    prior = asyncio.run(round_memory.prior_for(sprint, tmp_path / "plan.md"))
    assert (prior.verdict, prior.since, prior.diff) == (FAIL, "HEAD", "")


def test_reset_forgets_an_earlier_loop(sprint):
    sprint.config.update({
        round_memory.VERDICT_KEY: FAIL,
        round_memory.REPLY_KEY: "old",
        round_memory.TREE_KEY: "abc",
    })
    round_memory.reset(sprint)
    assert asyncio.run(round_memory.prior_for(sprint, sprint.repo_dir / "p.md")) is None
    assert round_memory.REPLY_KEY not in sprint.config


def test_legacy_round_loop_feeds_the_next_review(sprint, tmp_path):
    plan = tmp_path / "plan.md"
    review = AsyncMock(side_effect=[("FAIL", FAIL), ("PASS", PASS)])
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock()
    generator.implement = AsyncMock(side_effect=["built", "answered"])
    with (
        patch("meow.execution.orchestrator.Generator", return_value=generator),
        patch("meow.execution.orchestrator.ReviewerAgent.review_plan", new=review),
    ):
        assert asyncio.run(orchestrator._run_rounds(sprint, plan)) is True
    prior = review.await_args_list[1].kwargs["prior"]
    assert (prior.verdict, prior.reply) == (FAIL, "answered")


def test_initial_review_file_verdict_seeds_the_memory(sprint, tmp_path):
    plan = tmp_path / "plan.md"
    review = AsyncMock(return_value=("PASS", PASS))
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock()
    generator.implement = AsyncMock(return_value="fixed")
    with (
        patch("meow.execution.orchestrator.Generator", return_value=generator),
        patch("meow.execution.orchestrator.ReviewerAgent.review_plan", new=review),
    ):
        asyncio.run(
            orchestrator._run_review_rounds(
                sprint, plan, initial_verdict=("FAIL", FAIL)
            )
        )
    assert review.await_args.kwargs["prior"].verdict == FAIL

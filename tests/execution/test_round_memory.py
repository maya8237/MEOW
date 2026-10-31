import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meow.agents.reviewer import (
    MAX_MEMORY_CHARS,
    PriorReview,
    criteria_section,
    plan_review_query,
)
from meow.agents.tester import handoff_section
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


CRITERIA_VERDICT = (
    "SUMMARY: ok\nSTATUS: PASS\n\n## Criteria\n- PASS: adds flag\n- FAIL: no crash\n\n"
    "## Notes\nlater"
)
TESTER_FAIL = "SUMMARY: broke\nSTATUS: FAIL\n\n## Test plan\n- case A: FAIL"


def _gate(sprint, tmp_path, reviews, testers):
    plan = tmp_path / "plan.md"
    test = AsyncMock(side_effect=testers)
    with (
        patch(
            "meow.execution.orchestrator.ReviewerAgent.review_plan",
            new=AsyncMock(side_effect=reviews),
        ),
        patch("meow.execution.orchestrator.VerificationAgent.test_plan", new=test),
        patch("meow.execution.orchestrator.prepared_test_stage") as stage,
    ):
        stage.return_value.__aenter__.return_value = MagicMock(
            commands=[], browser=[], blocking_failed=False
        )
        for round_num in (1, 2):
            if round_num == SECOND_ROUND:
                (tmp_path / "code.py").write_text("x = 2\n", encoding="utf-8")
            asyncio.run(orchestrator.review_then_test(sprint, plan, round_num))
    return test


def test_criteria_section_is_tolerant():
    assert criteria_section(CRITERIA_VERDICT) == "- PASS: adds flag\n- FAIL: no crash"
    assert criteria_section("SUMMARY: x\nSTATUS: PASS\n## Criteria\n- PASS: a") == (
        "- PASS: a"
    )
    assert criteria_section(PASS) == ""


def test_tester_gets_criteria_and_then_its_prior_verdict(sprint, tmp_path):
    test = _gate(
        sprint,
        tmp_path,
        [("PASS", CRITERIA_VERDICT)] * 2,
        [("FAIL", TESTER_FAIL), ("PASS", PASS)],
    )
    first, second = (call.kwargs for call in test.await_args_list)
    assert first["criteria"] == "- PASS: adds flag\n- FAIL: no crash"
    assert first["prior"] is None
    assert second["prior"].verdict == TESTER_FAIL
    assert second["prior"].since == "previous test"
    assert "code.py" in second["prior"].stat


def test_reset_forgets_the_tester_round(sprint, tmp_path):
    asyncio.run(round_memory.remember_test(sprint, tmp_path / "plan.md", TESTER_FAIL))
    round_memory.reset(sprint)
    assert (
        asyncio.run(round_memory.prior_test_for(sprint, tmp_path / "plan.md")) is None
    )


def test_tester_message_carries_the_handoff():
    text = handoff_section(
        "- PASS: a", PriorReview(TESTER_FAIL, "", " code.py | 2", "")
    )
    assert "Reviewer-graded criteria (verify each by observed behavior" in text
    assert "- PASS: a" in text
    assert "Previous tester verdict" in text
    assert "code.py" in text
    assert handoff_section("", None) == ""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli.review_cli import run_review_command


def test_explicit_project_scope_bypasses_latest_plan(tmp_path):
    config = {"lint": []}
    with (
        patch("meow.cli.review_cli.onboard_if_needed"),
        patch("meow.cli.review_cli.load_config", return_value=config),
        patch("meow.cli.review_cli._prompt_review", new_callable=AsyncMock) as review,
        patch(
            "meow.cli.review_cli._plan_or_prompt_review", new_callable=AsyncMock
        ) as plan,
    ):
        asyncio.run(run_review_command(tmp_path, None, fix=False, scope="project"))
    review.assert_awaited_once_with(tmp_path, config, None, fix=False)
    plan.assert_not_awaited()
    assert config["_review_scope"] == "project"


@pytest.mark.parametrize(
    "source",
    [
        {"plan_file": Path("plan.md")},
        {"gitlab_link": "url"},
        {"github_link": "url"},
        {"branch": "topic", "target": "main"},
    ],
)
def test_project_scope_rejects_diff_or_plan_sources(tmp_path, source):
    with (
        patch("meow.cli.review_cli.load_config") as load,
        pytest.raises(ValueError, match="scope"),
    ):
        asyncio.run(
            run_review_command(tmp_path, None, fix=False, scope="project", **source)
        )
    load.assert_not_called()


def test_explicit_diff_scope_bypasses_latest_plan(tmp_path):
    config = {"lint": []}
    with (
        patch("meow.cli.review_cli.onboard_if_needed"),
        patch("meow.cli.review_cli.load_config", return_value=config),
        patch("meow.cli.review_cli._prompt_review", new_callable=AsyncMock) as review,
    ):
        asyncio.run(run_review_command(tmp_path, None, fix=False, scope="diff"))
    review.assert_awaited_once_with(tmp_path, config, None, fix=False)


def test_resumed_pass_is_gated_by_current_required_checks(tmp_path):
    from meow.cli.review_cli import _resume_review_file
    from meow.infrastructure.lint import LintGateEvidence
    from meow.project.review_scope import prepare_scope

    report = tmp_path / ".meow/review.md"
    prepare_scope(report, tmp_path, ".meow/plans", "changes", True, "diff")
    report.write_text("SUMMARY: old success\nSTATUS: PASS\n")
    with (
        patch(
            "meow.cli.review_cli.ReviewerAgent._lint_evidence",
            new_callable=AsyncMock,
            return_value=LintGateEvidence(("required tests failed",), ()),
        ),
        patch(
            "meow.cli.review_cli._run_prompt_fix_rounds",
            new_callable=AsyncMock,
            return_value=True,
        ) as rounds,
    ):
        asyncio.run(_resume_review_file(tmp_path, {"max_rounds": 2}, None, report))
    assert rounds.call_args.args[1][0] == "FAIL"
    assert "STATUS: FAIL" in report.read_text()


def test_resume_preserves_project_scope_for_further_rounds(tmp_path):
    from meow.cli.review_cli import _resume_review_file
    from meow.infrastructure.lint import LintGateEvidence
    from meow.project.review_scope import prepare_scope

    report = tmp_path / ".meow/review.md"
    prepare_scope(report, tmp_path, ".meow/plans", "whole project", True)
    report.write_text("SUMMARY: coverage missing\nSTATUS: FAIL\n")
    config = {"max_rounds": 2}
    with (
        patch(
            "meow.cli.review_cli.ReviewerAgent._lint_evidence",
            new_callable=AsyncMock,
            return_value=LintGateEvidence(),
        ),
        patch(
            "meow.cli.review_cli._run_prompt_fix_rounds",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        asyncio.run(_resume_review_file(tmp_path, config, None, report))
    assert config["_review_scope"] == "project"


def test_legacy_saved_pass_gets_fresh_review_before_any_fixer(tmp_path):
    from meow.cli.review_cli import _resume_review_file

    report = tmp_path / "review.md"
    report.write_text("SUMMARY: old\nSTATUS: PASS\n")
    with (
        patch(
            "meow.cli.review_cli.ReviewerAgent.review_prompt",
            new_callable=AsyncMock,
            return_value=("PASS", "SUMMARY: fresh\nSTATUS: PASS\n"),
        ) as reviewer,
        patch(
            "meow.cli.review_cli._run_prompt_fix_rounds",
            new_callable=AsyncMock,
            return_value=True,
        ) as rounds,
    ):
        asyncio.run(_resume_review_file(tmp_path, {"max_rounds": 1}, None, report))
    reviewer.assert_awaited_once_with(None)
    assert rounds.call_args.args[1][0] == "PASS"

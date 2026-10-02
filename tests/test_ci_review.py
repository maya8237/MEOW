import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.ci_review import CiReviewError, prepare_ci_review, run_ci_review


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "dev")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "feature/x")
    (repo / "file.txt").write_text("feature edit\n")
    git(repo, "commit", "-am", "feature")
    source = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", source)
    env = {
        "CI_PIPELINE_SOURCE": "push",
        "CI_COMMIT_BRANCH": "feature/x",
        "CI_COMMIT_SHA": source,
    }
    return repo, base, source, env


def test_detached_preflight_uses_fixed_sha_and_diff(checkout):
    repo, base, source, env = checkout
    context = prepare_ci_review(repo, env)
    assert (context.source_sha, context.target_sha, context.merge_base) == (
        source,
        base,
        base,
    )
    assert "feature edit" in context.diff
    git(repo, "checkout", "dev")
    (repo / "file.txt").write_text("moved\n")
    git(repo, "commit", "-am", "move target")
    assert context.target_sha == base
    assert "feature edit" in context.diff


@pytest.mark.parametrize(
    "change",
    [
        {"CI_COMMIT_SHA": "a" * 40},
        {"CI_COMMIT_SHA": "bad"},
        {"CI_COMMIT_SHA": ""},
        {"CI_PIPELINE_SOURCE": "schedule"},
        {"CI_COMMIT_BRANCH": ""},
    ],
)
def test_invalid_context_fails_before_review(checkout, change):
    repo, _, _, env = checkout
    with pytest.raises(CiReviewError):
        prepare_ci_review(repo, env | change)


def test_missing_target_history_fails(checkout):
    repo, _, _, env = checkout
    with pytest.raises(CiReviewError, match="target ref unavailable"):
        prepare_ci_review(repo, env, "missing")


def test_detached_merge_request_pipeline_to_dev(checkout):
    repo, _, source, env = checkout
    env = {
        **env,
        "CI_PIPELINE_SOURCE": "merge_request_event",
        "CI_MERGE_REQUEST_EVENT_TYPE": "detached",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/x",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "dev",
        "CI_COMMIT_BRANCH": "",
    }
    assert prepare_ci_review(repo, env).source_sha == source
    with pytest.raises(CiReviewError):
        prepare_ci_review(repo, env | {"CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main"})
    with pytest.raises(CiReviewError):
        prepare_ci_review(repo, env | {"CI_MERGE_REQUEST_EVENT_TYPE": "merged_result"})
    without_type = {
        key: value for key, value in env.items()
        if key != "CI_MERGE_REQUEST_EVENT_TYPE"
    }
    with pytest.raises(CiReviewError):
        prepare_ci_review(repo, without_type)


def test_example_job_runs_only_for_detached_mrs_to_dev():
    root = Path(__file__).resolve().parents[1]
    example = (root / "templates/gitlab-ci-review.yml").read_text()
    assert "templates/gitlab-ci-review.yml" in (root / ".gitlab-ci.yml").read_text()
    assert 'CI_PIPELINE_SOURCE == "merge_request_event"' in example
    assert 'CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "dev"' in example
    assert 'CI_MERGE_REQUEST_EVENT_TYPE == "detached"' in example
    assert "- when: never" in example


@pytest.mark.parametrize(
    ("reply", "verdict", "code"),
    [
        ("SUMMARY: Fine\nSTATUS: PASS", "PASS", 0),
        ("SUMMARY: Bug\nSTATUS: FAIL", "FAIL", 1),
        ("No verdict", "UNVERIFIED", 2),
    ],
)
def test_runner_artifacts_and_exit(checkout, reply, verdict, code):
    repo, base, source, env = checkout
    artifacts = repo / ".meow-ci-artifacts"
    with patch(
        "meow.ci_review.ReviewerAgent.review_ci_branch",
        new_callable=AsyncMock,
        return_value=(verdict, reply),
    ):
        result = run_ci_review(
            repo, {"models": {"reviewer": None}}, env, "dev", artifacts, None
        )
    assert result.exit_code == code
    payload = json.loads(result.json_path.read_text())
    assert payload["source_sha"] == source
    assert payload["target_sha"] == base
    assert payload["merge_base"] == base
    assert payload["verdict"] == verdict
    assert result.report_path.exists()


def test_sdk_failure_redacts_secret(checkout):
    repo, _, _, env = checkout
    env["ANTHROPIC_API_KEY"] = "private-key"
    with patch(
        "meow.ci_review.ReviewerAgent.review_ci_branch",
        new_callable=AsyncMock,
        side_effect=RuntimeError("private-key failed"),
    ):
        result = run_ci_review(
            repo,
            {"models": {"reviewer": None}},
            env,
            "dev",
            repo / ".meow-ci-artifacts",
            None,
        )
    assert result.exit_code == 2  # ruff: ignore[magic-value-comparison] -- infrastructure exit status
    assert "private-key" not in result.report_path.read_text()
    assert "private-key" not in result.json_path.read_text()


def test_untracked_checkout_write_invalidates_pass(checkout):
    repo, _, _, env = checkout

    async def writes_file(_self, _context):  # ruff: ignore[unused-async] -- SDK seam
        (repo / "unexpected.py").write_text("new code")
        return "PASS", "SUMMARY: Fine\nSTATUS: PASS"

    with patch("meow.ci_review.ReviewerAgent.review_ci_branch", writes_file):
        result = run_ci_review(
            repo,
            {"models": {"reviewer": None}},
            env,
            "dev",
            repo / ".meow-ci-artifacts",
            None,
        )
    assert result.verdict == "UNVERIFIED"
    assert "checkout HEAD or files changed" in result.report_path.read_text()


def test_ci_reviewer_grants_only_read_tools(checkout):
    from meow.agents.base import ProjectContext
    from meow.agents.reviewer import ReviewerAgent

    repo, _, _, env = checkout
    context = prepare_ci_review(repo, env)
    agent = ReviewerAgent(ProjectContext(repo, {"models": {"reviewer": None}}))

    async def sdk_stream(*, prompt, options):  # ruff: ignore[unused-async] -- SDK async iterator seam
        assert options.allowed_tools == ["Read", "Grep", "Glob"]
        assert options.tools == ["Read", "Grep", "Glob"]
        assert options.strict_mcp_config is True
        assert options.permission_mode == "dontAsk"
        assert "feature edit" in prompt
        from claude_agent_sdk import ResultMessage

        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0,
            result="SUMMARY: Fine\nSTATUS: PASS",
        )

    with patch("meow.agents.reviewer.query", sdk_stream):
        import asyncio

        status, _ = asyncio.run(agent.review_ci_branch(context))
    assert status == "PASS"

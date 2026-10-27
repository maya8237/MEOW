import asyncio
import json
import re
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.infrastructure.usage import usage_scope
from meow.integrations.ci_comments import merge_shard_responses
from meow.integrations.ci_review import (
    CiReviewContext,
    CiReviewError,
    CiShard,
    _review,
    _sharded_diff,
    prepare_ci_review,
    run_ci_review,
)


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


def test_merge_request_description_is_review_context(checkout):
    repo, _, _, env = checkout
    env = {
        **env,
        "CI_PIPELINE_SOURCE": "merge_request_event",
        "CI_MERGE_REQUEST_EVENT_TYPE": "detached",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/x",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "dev",
        "CI_MERGE_REQUEST_DESCRIPTION": "Must preserve the old behavior.",
        "CI_MERGE_REQUEST_DESCRIPTION_IS_TRUNCATED": "false",
        "CI_COMMIT_BRANCH": "",
    }
    context = prepare_ci_review(repo, env)
    assert context.mr_description == "Must preserve the old behavior."
    assert not context.mr_description_truncated


def test_merge_request_must_target_the_configured_branch(checkout):
    repo, _, _, env = checkout
    env |= {
        "CI_PIPELINE_SOURCE": "merge_request_event",
        "CI_MERGE_REQUEST_EVENT_TYPE": "detached",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/x",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
    }
    with pytest.raises(CiReviewError, match="must target dev"):
        prepare_ci_review(repo, env)
    context = prepare_ci_review(repo, env, "dev", target_branch="main")
    assert context.source_ref == "feature/x"


def test_truncated_merge_request_description_is_flagged(checkout):
    repo, _, _, env = checkout
    env |= {
        "CI_PIPELINE_SOURCE": "merge_request_event",
        "CI_MERGE_REQUEST_EVENT_TYPE": "detached",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/x",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "dev",
        "CI_MERGE_REQUEST_DESCRIPTION": "Partial brief",
        "CI_MERGE_REQUEST_DESCRIPTION_IS_TRUNCATED": "true",
    }
    assert prepare_ci_review(repo, env).mr_description_truncated


def test_example_job_runs_for_merge_request_pipelines():
    root = Path(__file__).resolve().parents[2]
    example = (root / "templates/gitlab-ci-review.yml").read_text()
    assert 'CI_PIPELINE_SOURCE == "merge_request_event"' in example
    assert 'CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "dev"' in example
    assert "- when: never" in example
    assert "image: python:3.12" in example
    assert "MEOW_INSTALL_SPEC" not in example


def test_ci_integrations_install_the_published_distribution():
    root = Path(__file__).resolve().parents[2]
    distribution = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "name"
    ]
    for relative in (
        "templates/gitlab-ci-review.yml",
        "skills/integrate-ci-review/SKILL.md",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        packages = re.findall(r"python -m pip install ([\w-]+)", text)
        assert packages and set(packages) == {distribution}, relative


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
    artifacts = repo / ".meow" / "ci-artifacts"
    with patch(
        "meow.integrations.ci_review.ReviewerAgent.review_ci_branch",
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
        "meow.integrations.ci_review.ReviewerAgent.review_ci_branch",
        new_callable=AsyncMock,
        side_effect=RuntimeError("private-key failed"),
    ):
        result = run_ci_review(
            repo,
            {"models": {"reviewer": None}},
            env,
            "dev",
            repo / ".meow" / "ci-artifacts",
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

    with patch(
        "meow.integrations.ci_review.ReviewerAgent.review_ci_branch", writes_file
    ):
        result = run_ci_review(
            repo,
            {"models": {"reviewer": None}},
            env,
            "dev",
            repo / ".meow" / "ci-artifacts",
            None,
        )
    assert result.verdict == "UNVERIFIED"
    assert "checkout HEAD or files changed" in result.report_path.read_text()


def test_ci_reviewer_grants_only_read_tools(  # ruff: ignore[too-many-statements] -- SDK stream assertions cover the review contract
    checkout,
):
    from meow.agents.base import ProjectContext
    from meow.agents.reviewer import ReviewerAgent

    repo, _, _, env = checkout
    context = prepare_ci_review(
        repo,
        env
        | {
            "CI_PIPELINE_SOURCE": "merge_request_event",
            "CI_MERGE_REQUEST_EVENT_TYPE": "detached",
            "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/x",
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "dev",
            "CI_MERGE_REQUEST_DESCRIPTION": "Must preserve the old behavior.",
            "CI_MERGE_REQUEST_DESCRIPTION_IS_TRUNCATED": "true",
        },
    )
    agent = ReviewerAgent(ProjectContext(repo, {"models": {"reviewer": None}}))

    from dataclasses import replace

    context = replace(
        context, existing_comments='{"comments": [{"body": "Existing concern"}]}'
    )

    async def sdk_stream(*, prompt, options):  # ruff: ignore[unused-async] -- SDK async iterator seam
        assert options.allowed_tools == ["Read", "Grep", "Glob"]
        assert options.tools == ["Read", "Grep", "Glob"]
        assert options.strict_mcp_config is True
        assert options.permission_mode == "dontAsk"
        assert "feature edit" in prompt
        assert "Must preserve the old behavior." in prompt
        assert "truncated" in prompt
        assert "Existing concern" in prompt
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

    store = RunStore(repo)
    record = store.create(
        source="review", request="review", repo=repo, worktree=repo, branch="detached"
    )
    with usage_scope(store, record.id), patch("meow.agents.reviewer.query", sdk_stream):
        import asyncio

        status, _ = asyncio.run(agent.review_ci_branch(context))
    assert status == "PASS"
    assert len(store.load(record.id).usage["entries"]) == 1


@pytest.mark.parametrize(
    "filename", ["file.txt", "existing-untracked.txt", " leading café.txt"]
)
def test_rewriting_already_dirty_file_invalidates_pass(checkout, filename):
    repo, _, _, env = checkout
    changed = repo / filename
    changed.write_text("before review\n")

    async def rewrites_file(_self, _context):  # ruff: ignore[unused-async] -- SDK seam
        changed.write_text("changed during review\n")
        return "PASS", "SUMMARY: Fine\nSTATUS: PASS"

    with patch(
        "meow.integrations.ci_review.ReviewerAgent.review_ci_branch", rewrites_file
    ):
        result = run_ci_review(
            repo, {}, env, "dev", repo / ".meow" / "ci-artifacts", None
        )
    assert result.verdict == "UNVERIFIED"
    assert "checkout HEAD or files changed" in result.report_path.read_text()


def test_rewriting_dirty_index_without_worktree_change_invalidates_pass(checkout):
    repo, _, _, env = checkout
    changed = repo / "file.txt"
    changed.write_text("staged before review\n")
    git(repo, "add", "file.txt")
    working_content = "unchanged working tree\n"
    changed.write_text(working_content)
    initial_status = git(repo, "status", "--porcelain", "--", "file.txt")

    async def rewrites_index(_self, _context):  # ruff: ignore[unused-async] -- SDK seam
        changed.write_text("staged during review\n")
        git(repo, "add", "file.txt")
        changed.write_text(working_content)
        return "PASS", "SUMMARY: Fine\nSTATUS: PASS"

    with patch(
        "meow.integrations.ci_review.ReviewerAgent.review_ci_branch", rewrites_index
    ):
        result = run_ci_review(
            repo, {}, env, "dev", repo / ".meow" / "ci-artifacts", None
        )

    assert git(repo, "status", "--porcelain", "--", "file.txt") == initial_status
    assert changed.read_text() == working_content
    assert git(repo, "show", ":file.txt") == "staged during review"
    assert result.verdict == "UNVERIFIED"
    assert "checkout HEAD or files changed" in result.report_path.read_text()


def commit_files(repo, files):
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "more")
    return git(repo, "rev-parse", "HEAD")


def test_oversized_diff_is_sharded_and_unreviewable_files_listed(checkout):
    repo, base, _, env = checkout
    head = commit_files(repo, {"huge.txt": "x\n" * 150_000, "small.txt": "small\n"})
    context = prepare_ci_review(
        repo, {**env, "CI_COMMIT_SHA": head}, base, target_branch="dev"
    )
    assert context.omitted_files == ("huge.txt",)
    assert [s.files for s in context.shards] == [("file.txt", "small.txt")]
    assert "huge.txt" not in context.diff


def test_shards_group_sorted_files_by_count(checkout, monkeypatch):
    repo, base, _, _ = checkout
    monkeypatch.setattr("meow.integrations.ci_review.FILES_PER_SHARD", 2)
    monkeypatch.setattr("meow.integrations.ci_review.MAX_SHARDS", 2)
    names = ["a/1.txt", "a/2.txt", "b/1.txt", "b/2.txt", "c.txt"]
    for name in names:
        (repo / name).parent.mkdir(exist_ok=True)
    head = commit_files(repo, {name: "text\n" for name in names})
    shards, omitted = _sharded_diff(repo, base, head)
    assert [s.files for s in shards] == [("a/1.txt", "a/2.txt"), ("b/1.txt", "b/2.txt")]
    assert omitted == ("c.txt", "file.txt")


def test_sharded_review_merges_verdicts_and_comment_blocks(checkout):
    repo, base, source, _ = checkout
    shards = (CiShard(("a.py",), "diff a"), CiShard(("b.py",), "diff b"))
    context = CiReviewContext(
        source, "feature/x", "dev", base, base, "d", shards=shards
    )
    replies = {
        "diff a": ("PASS", 'ok A\n```meow-comments\n[{"path": "a.py"}]\n```'),
        "diff b": ("FAIL", 'bad B\n```meow-comments\n[{"path": "b.py"}]\n```'),
    }

    async def review(self, ctx):
        await asyncio.sleep(0)
        assert ctx.shard_label and ctx.other_files
        return replies[ctx.diff]

    with patch("meow.integrations.ci_review.ReviewerAgent.review_ci_branch", review):
        verdict, text = asyncio.run(_review(repo, {}, context))
    assert verdict == "FAIL"
    assert text.count("```meow-comments") == 1
    assert '"a.py"' in text and '"b.py"' in text
    assert "## Shard 2 of 2" in text


def test_merge_leaves_malformed_blocks_for_publishing_to_reject():
    broken = "```meow-comments\nnot json\n```"
    merged = merge_shard_responses([broken, "```meow-comments\n[]\n```"])
    expected_blocks = 2
    assert merged.count("```meow-comments") == expected_blocks

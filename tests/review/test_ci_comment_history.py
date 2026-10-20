import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from meow.integrations.ci_comments import post_review_comments
from meow.integrations.ci_review import CiReviewContext, CiReviewError, run_ci_review
from tests.review.test_ci_comments import reply


@pytest.fixture
def checkout(tmp_path):
    from tests.review.test_ci_review import checkout as fixture

    return fixture.__wrapped__(tmp_path)


@pytest.fixture(params=["github", "gitlab"])
def api(request, tmp_path):
    provider = request.param
    event = tmp_path / "event.json"
    event.write_text('{"number": 7}')
    env = {
        "MEOW_GITHUB_TOKEN": "secret",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "owner/repo",
        "MEOW_GITLAB_TOKEN": "secret",
        "CI_API_V4_URL": "https://gitlab.example/api/v4",
        "CI_PROJECT_ID": "12",
        "CI_MERGE_REQUEST_IID": "3",
    }
    return provider, env


def fake_api(provider, context, history):  # ruff: ignore[complex-structure] -- paginated provider API test seam
    posted = []
    reads = []

    def request(url, token, payload=None):  # ruff: ignore[too-many-return-statements] -- provider API test seam
        if payload is not None:
            posted.append(payload)
            return {}
        reads.append(url)
        if "?" in url:
            if "/issues/" in url:
                return [{"body": "Conversation concern"}]
            if "/reviews?" in url:
                return [{"body": "Summary concern"}]
            start = (int(parse_qs(urlsplit(url).query)["page"][0]) - 1) * 100
            return history[start : start + 100]
        if provider == "github":
            return {
                "head": {"sha": context.source_sha},
                "base": {"sha": context.target_sha},
            }
        return {
            "diff_refs": {
                "base_sha": context.merge_base,
                "start_sha": context.target_sha,
                "head_sha": context.source_sha,
            }
        }

    return request, posted, reads


def existing(provider, sha="head", body="Bug here"):
    if provider == "github":
        return {
            "id": 1,
            "path": "file.txt",
            "line": 1,
            "side": "RIGHT",
            "body": body,
            "commit_id": sha,
        }
    return {
        "id": "thread",
        "notes": [
            {
                "id": 1,
                "body": body,
                "resolved": False,
                "position": {
                    "head_sha": sha,
                    "new_path": "file.txt",
                    "old_path": "file.txt",
                    "new_line": 1,
                },
            }
        ],
    }


def test_existing_and_repeated_findings_are_not_posted(api):
    provider, env = api
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    history = [existing(provider, body=f"Other concern {i}") for i in range(100)]
    history.append(existing(provider))
    request, posted, _ = fake_api(provider, context, history)
    duplicate = {"path": "file.txt", "line": 1, "side": "RIGHT", "body": " Bug  here\n"}
    new = duplicate | {"body": "A different bug"}
    with patch("meow.integrations.ci_comments._request", request):
        count = post_review_comments(
            provider, context, reply([duplicate, new, new]), env
        )
    assert count == 1
    assert [item["body"] for item in posted] == ["A different bug"]


def test_history_is_paginated_and_reaches_reviewer(api, checkout, tmp_path):
    provider, env = api
    repo, base, source, ci_env = checkout
    context = CiReviewContext(source, "feature", "dev", base, base, "")
    history = [existing(provider, source, f"Existing concern {i}") for i in range(100)]
    request, posted, reads = fake_api(provider, context, history)

    async def review(_self, reviewed):  # ruff: ignore[unused-async] -- SDK seam
        assert "Existing concern 99" in reviewed.existing_comments
        if provider == "github":
            assert "Conversation concern" in reviewed.existing_comments
            assert "Summary concern" in reviewed.existing_comments
        return "PASS", reply([])

    with (
        patch("meow.integrations.ci_comments._request", request),
        patch("meow.integrations.ci_review.ReviewerAgent.review_ci_branch", review),
    ):
        result = run_ci_review(
            repo,
            {},
            ci_env | env,
            "dev",
            tmp_path / "artifacts",
            None,
            post_comments=provider,
        )
    assert result.verdict == "PASS"
    assert any("page=2&" in url for url in reads)
    assert posted == []


def test_unreadable_history_prevents_review_and_posting(api, checkout, tmp_path):
    provider, env = api
    repo, _, _, ci_env = checkout
    with (
        patch("meow.integrations.ci_comments._request", return_value={}),
        patch(
            "meow.integrations.ci_review.ReviewerAgent.review_ci_branch",
            new_callable=AsyncMock,
        ) as review,
    ):
        result = run_ci_review(
            repo,
            {},
            ci_env | env,
            "dev",
            tmp_path / "artifacts",
            None,
            post_comments=provider,
        )
    assert result.verdict == "UNVERIFIED"
    review.assert_not_called()
    assert "comments" in json.loads(result.json_path.read_text())["failure_reason"]


def test_outdated_location_does_not_suppress_current_finding(api):
    provider, env = api
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    stale = existing(provider, sha="previous")
    if provider == "github":
        stale.update(line=None, original_line=1)
    request, posted, _ = fake_api(provider, context, [stale])
    finding = {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Bug here"}
    with patch("meow.integrations.ci_comments._request", request):
        assert post_review_comments(provider, context, reply([finding]), env) == 1
    assert len(posted) == 1


def test_partial_publish_retry_skips_successfully_posted_notes(api):
    provider, env = api
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    history = []
    request, posted, _ = fake_api(provider, context, history)
    first = {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Bug here"}
    second = first | {"body": "Another bug"}

    def partial(url, token, payload=None):
        if payload is not None:
            if history:
                raise CiReviewError("publish interrupted")
            history.append(existing(provider))
        return request(url, token, payload)

    with (
        patch("meow.integrations.ci_comments._request", partial),
        pytest.raises(CiReviewError, match="interrupted"),
    ):
        post_review_comments(provider, context, reply([first, second]), env)
    with patch("meow.integrations.ci_comments._request", request):
        assert post_review_comments(provider, context, reply([first, second]), env) == 1
    assert [item["body"] for item in posted] == ["Bug here", "Another bug"]


def test_comments_arriving_during_review_are_checked_before_posting(
    api, checkout, tmp_path
):
    provider, env = api
    repo, base, source, ci_env = checkout
    context = CiReviewContext(source, "feature", "dev", base, base, "")
    history = []
    request, posted, _ = fake_api(provider, context, history)

    async def review(_self, reviewed):  # ruff: ignore[unused-async] -- SDK seam
        history.append(existing(provider, sha=source))
        return "FAIL", reply([
            {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Bug here"}
        ])

    with (
        patch("meow.integrations.ci_comments._request", request),
        patch("meow.integrations.ci_review.ReviewerAgent.review_ci_branch", review),
    ):
        result = run_ci_review(
            repo,
            {},
            ci_env | env,
            "dev",
            tmp_path / "artifacts",
            None,
            post_comments=provider,
        )
    assert result.verdict == "FAIL"
    assert json.loads(result.json_path.read_text())["comments_posted"] == 0
    assert posted == []

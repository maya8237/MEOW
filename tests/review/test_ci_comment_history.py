import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from meow.integrations.ci_comments import (
    MARKER,
    parse_resolutions,
    post_review_comments,
    resolve_review_comments,
)
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


def threads_response(resolved=False):
    root = {"databaseId": 1, "body": "Bug here\n\n" + MARKER}
    node = {"id": "T1", "isResolved": resolved, "comments": {"nodes": [root]}}
    page = {"pageInfo": {"hasNextPage": False}, "nodes": [node]}
    return {"data": {"repository": {"pullRequest": {"reviewThreads": page}}}}


def fake_api(provider, context, history, *, thread_resolved=False):  # ruff: ignore[complex-structure] -- paginated provider API test seam
    posted = []
    reads = []
    graphql = threads_response(thread_resolved)

    def request(url, token, payload=None):  # ruff: ignore[complex-structure, too-many-return-statements] -- provider API test seam
        if payload is not None and "query" in payload:
            if "mutation" in payload["query"]:
                posted.append(payload)
                return {}
            return graphql
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
    assert [item["body"].split("\n\n<!--")[0] for item in posted] == ["A different bug"]


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


def outdated_case(provider, resolved):
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
        stale.update(line=None, original_line=7)
    else:
        stale["notes"][0].update(resolved=resolved)
    request, posted, _ = fake_api(provider, context, [stale], thread_resolved=resolved)
    return context, request, posted


@pytest.mark.parametrize("resolved", [False, True])
def test_outdated_comment_suppresses_finding_only_while_open(api, resolved):
    provider, env = api
    context, request, posted = outdated_case(provider, resolved)
    finding = {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Bug here"}
    with patch("meow.integrations.ci_comments._request", request):
        count = post_review_comments(provider, context, reply([finding]), env)
    assert count == (1 if resolved else 0)
    assert len(posted) == count


def test_reworded_finding_on_same_line_is_duplicate(api):
    provider, env = api
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    request, posted, _ = fake_api(provider, context, [existing(provider)])
    reworded = {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Bug here!"}
    different = reworded | {"body": "Completely unrelated null dereference"}
    with patch("meow.integrations.ci_comments._request", request):
        count = post_review_comments(
            provider, context, reply([reworded, different]), env
        )
    assert count == 1
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
        if payload is not None and "query" not in payload:
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
    assert [item["body"].split("\n\n<!--")[0] for item in posted] == [
        "Bug here",
        "Another bug",
    ]


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


def gitlab_thread(body="Bug here\n\n" + MARKER, resolved=False):
    note = {"id": 1, "body": body, "resolvable": True, "resolved": resolved}
    return {"id": "abc", "notes": [note]}


def block(items):
    return "```meow-resolutions\n" + json.dumps(items) + "\n```"


def test_resolutions_are_optional_and_validated():
    assert parse_resolutions("no block") == []
    assert parse_resolutions(block([{"id": 1, "reason": "Fixed"}])) == [
        {"id": 1, "reason": "Fixed"}
    ]
    for bad in ([{"id": 1}], [{"id": True, "reason": "x"}], [{"id": 1, "reason": " "}]):
        with pytest.raises(CiReviewError):
            parse_resolutions(block(bad))
    with pytest.raises(CiReviewError, match="at most one"):
        parse_resolutions(block([]) + "\n" + block([]))


def test_github_resolves_only_open_meow_threads(api):
    provider, env = api
    if provider != "github":
        pytest.skip("GitHub thread API")
    calls = []

    def request(url, token, payload=None, method=None):
        calls.append((url, payload, method))
        return threads_response() if "reviewThreads" in payload.get("query", "") else {}

    items = [{"id": 1, "reason": "Fixed"}, {"id": 99, "reason": "Unknown"}]
    with patch("meow.integrations.ci_comments._request", request):
        assert resolve_review_comments("github", items, env) == 1
    assert calls[1][1]["variables"] == {"id": "T1"}
    assert calls[2][0].endswith("/pulls/7/comments/1/replies")
    assert calls[2][1] == {"body": "Resolved: Fixed"}


def test_github_marks_solved_when_resolve_is_refused(api):
    provider, env = api
    if provider != "github":
        pytest.skip("GitHub thread API")
    calls = []

    def request(url, token, payload=None, method=None):
        calls.append((url, payload))
        query = payload.get("query", "")
        if "reviewThreads" in query:
            return threads_response()
        return {"errors": [{"message": "forbidden"}]} if "mutation" in query else {}

    with patch("meow.integrations.ci_comments._request", request):
        items = [{"id": 1, "reason": "Fixed"}]
        assert resolve_review_comments("github", items, env) == 1
    assert calls[-1][1] == {"body": "✅ Solved: Fixed"}


@pytest.mark.parametrize(
    "thread", [gitlab_thread(body="Human comment"), gitlab_thread(resolved=True)]
)
def test_gitlab_skips_human_and_resolved_threads(api, thread):
    provider, env = api
    if provider != "gitlab":
        pytest.skip("GitLab discussion API")
    calls = []

    def request(url, token, payload=None, method=None):
        calls.append((url, payload, method))
        return [thread] if "?" in url else {}

    with patch("meow.integrations.ci_comments._request", request):
        items = [{"id": "abc", "reason": "Fixed"}]
        assert resolve_review_comments("gitlab", items, env) == 0
    assert all(payload is None for _, payload, _ in calls)


def test_gitlab_replies_then_resolves_meow_thread(api):
    provider, env = api
    if provider != "gitlab":
        pytest.skip("GitLab discussion API")
    calls = []

    def request(url, token, payload=None, method=None):
        calls.append((url, payload, method))
        return [gitlab_thread()] if "?" in url else {}

    with patch("meow.integrations.ci_comments._request", request):
        items = [{"id": "abc", "reason": "Fixed"}]
        assert resolve_review_comments("gitlab", items, env) == 1
    assert calls[-2][0].endswith("/discussions/abc")
    assert calls[-2][1:] == ({"resolved": True}, "PUT")
    assert calls[-1][0].endswith("/discussions/abc/notes")
    assert calls[-1][1] == {"body": "Resolved: Fixed"}


def test_gitlab_marks_solved_when_resolve_is_refused(api):
    provider, env = api
    if provider != "gitlab":
        pytest.skip("GitLab discussion API")
    calls = []

    def request(url, token, payload=None, method=None):
        calls.append((url, payload, method))
        if "?" in url:
            return [gitlab_thread()]
        if method == "PUT":
            raise CiReviewError("review API failed (HTTP 403)")
        return {}

    with patch("meow.integrations.ci_comments._request", request):
        items = [{"id": "abc", "reason": "Fixed"}]
        assert resolve_review_comments("gitlab", items, env) == 1
    assert calls[-1][1] == {"body": "✅ Solved: Fixed"}


def test_long_reworded_finding_is_still_a_duplicate(api):
    provider, env = api
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    long_body = "The retry loop never resets the backoff counter after success. " * 5
    request, _, _ = fake_api(provider, context, [existing(provider, body=long_body)])
    finding = {
        "path": "file.txt",
        "line": 1,
        "side": "RIGHT",
        "body": long_body.replace("never resets", "does not reset"),
    }
    with patch("meow.integrations.ci_comments._request", request):
        assert post_review_comments(provider, context, reply([finding]), env) == 0


def test_gitlab_skips_thread_already_marked_solved(api):
    provider, env = api
    if provider != "gitlab":
        pytest.skip("GitLab discussion API")
    thread = gitlab_thread()
    thread["notes"].append({"id": 2, "body": "✅ Solved: Fixed"})
    calls = []

    def request(url, token, payload=None, method=None):
        calls.append(payload)
        return [thread] if "?" in url else {}

    with patch("meow.integrations.ci_comments._request", request):
        items = [{"id": "abc", "reason": "Fixed"}]
        assert resolve_review_comments("gitlab", items, env) == 0
    assert all(payload is None for payload in calls)


def test_github_threads_are_fetched_once_for_post_and_resolve(api):
    provider, env = api
    if provider != "github":
        pytest.skip("GitHub thread API")
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    request, _, _ = fake_api(provider, context, [existing(provider)])
    queries = []

    def counting(url, token, payload=None, method=None):
        if payload and "reviewThreads" in payload.get("query", ""):
            queries.append(url)
        return request(url, token, payload)

    state = {}
    with patch("meow.integrations.ci_comments._request", counting):
        post_review_comments(provider, context, reply([]), env, state)
        items = [{"id": 1, "reason": "Fixed"}]
        assert resolve_review_comments(provider, items, env, state) == 1
    assert len(queries) == 1


def test_unavailable_threads_are_reported_not_silent(api):
    from meow.integrations.ci_review import _publish_comments

    provider, env = api
    if provider != "github":
        pytest.skip("GitHub thread API")
    context = CiReviewContext(
        "head",
        "feature",
        "dev",
        "target",
        "base",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
    )
    request, _, _ = fake_api(provider, context, [])

    def no_graphql(url, token, payload=None, method=None):
        if payload and "query" in payload:
            raise CiReviewError("review API failed (HTTP 403)")
        return request(url, token, payload)

    with patch("meow.integrations.ci_comments._request", no_graphql):
        _, _, error = _publish_comments(provider, context, reply([]), env)
        assert "open-thread lookup unavailable" in error
        resolutions = block([{"id": 1, "reason": "Fixed"}])
        _, resolved, error = _publish_comments(
            provider, context, reply([]) + "\n" + resolutions, env
        )
    assert resolved is None
    assert "GraphQL" in error

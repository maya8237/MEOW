import json
from unittest.mock import AsyncMock, patch

import pytest

from meow.integrations.ci_comments import _findings, _gitlab_position, _request
from meow.integrations.ci_review import CiReviewContext, CiReviewError, run_ci_review


def reply(comments):
    return (
        "SUMMARY: Bug\nSTATUS: FAIL\n```meow-comments\n"
        + json.dumps(comments)
        + "\n```"
    )


def run(checkout, tmp_path, provider, comments, env=None):  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- CI test fixture inputs
    repo, _, _, ci_env = checkout
    with patch(
        "meow.integrations.ci_review.ReviewerAgent.review_ci_branch",
        new_callable=AsyncMock,
        return_value=("FAIL", reply(comments)),
    ):
        return run_ci_review(
            repo,
            {"models": {"reviewer": None}},
            ci_env | (env or {}),
            "dev",
            tmp_path / "artifacts",
            None,
            post_comments=provider,
        )


@pytest.fixture
def checkout(tmp_path):
    from tests.review.test_ci_review import checkout as fixture

    return fixture.__wrapped__(tmp_path)


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_missing_token_retains_artifacts(checkout, tmp_path, provider):
    result = run(checkout, tmp_path, provider, [])
    assert result.verdict == "UNVERIFIED"
    assert "TOKEN" in result.report_path.read_text()


def test_github_posts_inline_note(checkout, tmp_path):
    _, base, source, _ = checkout
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"number": 7}))
    comment = {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Bug here"}
    calls = []

    def request(url, token, payload=None):
        calls.append((url, token, payload))
        return (
            {"head": {"sha": source}, "base": {"sha": base}} if payload is None else {}
        )

    with patch("meow.integrations.ci_comments._request", request):
        result = run(
            checkout,
            tmp_path,
            "github",
            [comment],
            {
                "MEOW_GITHUB_TOKEN": "secret",
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_EVENT_NAME": "pull_request",
            },
        )
    assert result.verdict == "FAIL"
    assert calls[-1][0] == "https://api.github.com/repos/owner/repo/pulls/7/comments"
    assert calls[-1][2] == comment | {"commit_id": source}
    assert json.loads(result.json_path.read_text())["comments_posted"] == 1


def test_gitlab_posts_deleted_line_discussion(checkout, tmp_path):
    _, base, source, _ = checkout
    calls = []

    def request(url, token, payload=None):
        calls.append((url, token, payload))
        return {"diff_refs": {"base_sha": base, "start_sha": base, "head_sha": source}}

    with patch("meow.integrations.ci_comments._request", request):
        result = run(
            checkout,
            tmp_path,
            "gitlab",
            [
                {
                    "path": "file.txt",
                    "line": 1,
                    "side": "LEFT",
                    "body": "Regression",
                }
            ],
            {
                "MEOW_GITLAB_TOKEN": "secret",
                "CI_API_V4_URL": "https://gitlab.example/api/v4",
                "CI_PROJECT_ID": "12",
                "CI_MERGE_REQUEST_IID": "3",
            },
        )
    assert result.verdict == "FAIL"
    assert calls[-1][0].endswith("/projects/12/merge_requests/3/discussions")
    assert calls[-1][2] == {
        "body": "Regression",
        "position": {
            "position_type": "text",
            "base_sha": base,
            "start_sha": base,
            "head_sha": source,
            "old_path": "file.txt",
            "new_path": "file.txt",
            "old_line": 1,
        },
    }


def test_invalid_location_posts_nothing(checkout, tmp_path):
    with patch("meow.integrations.ci_comments._request") as request:
        result = run(
            checkout,
            tmp_path,
            "gitlab",
            [
                {"path": "file.txt", "line": 1, "side": "RIGHT", "body": "Valid"},
                {"path": "file.txt", "line": 99, "side": "RIGHT", "body": "Invalid"},
            ],
            {"MEOW_GITLAB_TOKEN": "secret"},
        )
    assert result.verdict == "UNVERIFIED"
    assert "diff" in result.report_path.read_text()
    request.assert_not_called()


def test_moved_head_posts_nothing(checkout, tmp_path):
    with patch(
        "meow.integrations.ci_comments._request",
        return_value={
            "diff_refs": {"head_sha": "moved"},
        },
    ) as request:
        result = run(
            checkout,
            tmp_path,
            "gitlab",
            [],
            {
                "MEOW_GITLAB_TOKEN": "secret",
                "CI_API_V4_URL": "https://gitlab.com/api/v4",
                "CI_PROJECT_ID": "12",
                "CI_MERGE_REQUEST_IID": "3",
            },
        )
    assert result.verdict == "UNVERIFIED"
    assert request.call_count == 1


@pytest.mark.parametrize(
    "comments",
    [
        {},
        [{"path": "x", "line": True, "side": "RIGHT", "body": "Bug"}],
        [{"path": [], "line": 1, "side": "RIGHT", "body": "Bug"}],
        [{"path": "x", "line": 1, "side": "RIGHT", "body": " "}],
    ],
)
def test_malformed_findings_are_rejected(comments):
    with pytest.raises(CiReviewError):
        _findings(reply(comments), "--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+new")


@pytest.mark.parametrize(
    "response",
    ["STATUS: PASS", reply([]) + "\n" + reply([]), "```meow-comments\ninvalid\n```"],
)
def test_missing_or_invalid_json_block_fails_closed(response):
    with pytest.raises(CiReviewError):
        _findings(response, "")


@pytest.mark.parametrize(
    ("old", "new", "side", "change"),
    [
        ("/dev/null", "b/new.txt", "RIGHT", "+new"),
        ("a/deleted.txt", "/dev/null", "LEFT", "-old"),
        ("a/old.txt", "b/new.txt", "RIGHT", "+new"),
    ],
)
def test_gitlab_positions_for_new_deleted_and_renamed_files(old, new, side, change):
    diff = f"--- {old}\n+++ {new}\n@@ -1 +1 @@\n{change}"
    path = (new if side == "RIGHT" else old)[2:]
    finding = {"path": path, "line": 1, "side": side, "body": "Bug"}
    assert _findings(reply([finding]), diff) == [finding]
    context = CiReviewContext("head", "feature", "dev", "target", "base", diff)
    position = _gitlab_position(context, finding, {})
    assert position["old_path"] != "/dev/null"
    assert position["new_path"] != "/dev/null"
    assert position["new_line" if side == "RIGHT" else "old_line"] == 1


def test_http_failure_does_not_expose_credentials():
    from urllib.error import HTTPError

    with patch("meow.integrations.ci_comments.build_opener") as opener:
        opener.return_value.open.side_effect = HTTPError(
            "https://example.com",
            403,
            "secret-token",
            {},
            None,
        )
        with pytest.raises(CiReviewError, match=r"^review API failed \(HTTP 403\)$"):
            _request("https://example.com", "secret-token", {"body": "Bug"})


def test_http_and_redirects_are_rejected():
    from meow.integrations.ci_comments import _NoRedirect

    with pytest.raises(CiReviewError, match="HTTPS"):
        _request("http://example.com", "secret-token")
    with pytest.raises(CiReviewError, match="redirected"):
        _NoRedirect().redirect_request(
            None, None, 302, "Moved", {}, "https://other.com"
        )


@pytest.mark.parametrize(
    ("header", "path"),
    [
        ("b/a b.txt\t", "a b.txt"),
        ('"b/\\303\\251.txt"', "é.txt"),
        ('"b/a\\tb.txt"', "a\tb.txt"),
    ],
)
def test_quoted_and_whitespace_paths(header, path):
    finding = {"path": path, "line": 1, "side": "RIGHT", "body": "Bug"}
    diff = f"--- /dev/null\n+++ {header}\n@@ -0,0 +1 @@\n+new"
    assert _findings(reply([finding]), diff) == [finding]


def test_comment_contract_is_opt_in():
    from meow.project.prompts import ci_review_prompt

    assert "meow-comments" not in ci_review_prompt("head", "target", "base")
    assert "meow-comments" in ci_review_prompt(
        "head",
        "target",
        "base",
        post_comments=True,
    )


def test_http_request_uses_bearer_token_and_json_body():
    from io import BytesIO

    with patch("meow.integrations.ci_comments.build_opener") as opener:
        opener.return_value.open.return_value.__enter__.return_value = BytesIO(b"{}")
        assert _request("https://example.com", "secret-token", {"body": "Bug"}) == {}
    request = opener.return_value.open.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert json.loads(request.data) == {"body": "Bug"}


def test_posting_failure_retains_redacted_report(checkout, tmp_path):
    _, base, source, _ = checkout
    with patch(
        "meow.integrations.ci_comments._request",
        side_effect=[
            {"diff_refs": {"base_sha": base, "start_sha": base, "head_sha": source}},
            CiReviewError("secret-token failed"),
        ],
    ):
        result = run(
            checkout,
            tmp_path,
            "gitlab",
            [
                {
                    "path": "file.txt",
                    "line": 1,
                    "side": "RIGHT",
                    "body": "Bug",
                }
            ],
            {
                "MEOW_GITLAB_TOKEN": "secret-token",
                "CI_API_V4_URL": "https://gitlab.com/api/v4",
                "CI_PROJECT_ID": "12",
                "CI_MERGE_REQUEST_IID": "3",
            },
        )
    assert result.verdict == "UNVERIFIED"
    assert result.exit_code == 2  # ruff: ignore[magic-value-comparison] -- infrastructure status
    assert "[REDACTED] failed" in result.report_path.read_text()
    assert "secret-token" not in result.json_path.read_text()
    assert json.loads(result.json_path.read_text())["comments_posted"] is None


def test_long_report_does_not_truncate_comment_data(checkout, tmp_path):
    repo, _, _, env = checkout
    response = (
        "SUMMARY: Bug\nSTATUS: FAIL\n"
        + "Long report. " * 4000
        + "\n```meow-comments\n[]\n```"
    )
    with (
        patch(
            "meow.integrations.ci_review.ReviewerAgent.review_ci_branch",
            new_callable=AsyncMock,
            return_value=("FAIL", response),
        ),
        patch(
            "meow.integrations.ci_comments.post_review_comments", return_value=0
        ) as post,
    ):
        result = run_ci_review(
            repo,
            {"models": {"reviewer": None}},
            env,
            "dev",
            tmp_path / "artifacts",
            None,
            post_comments="github",
        )
    assert result.verdict == "FAIL"
    assert _findings(post.call_args.args[2], "") == []


def test_github_deleted_line_on_rename_uses_current_path(tmp_path):
    from meow.integrations.ci_comments import post_review_comments

    event = tmp_path / "event.json"
    event.write_text('{"number": 7}')
    diff = "--- a/old.txt\n+++ b/new.txt\n@@ -1 +1 @@\n-old\n+new"
    context = CiReviewContext("head", "feature", "dev", "target", "base", diff)
    with patch(
        "meow.integrations.ci_comments._request",
        return_value={
            "head": {"sha": "head"},
            "base": {"sha": "target"},
        },
    ) as request:
        post_review_comments(
            "github",
            context,
            reply([
                {
                    "path": "old.txt",
                    "line": 1,
                    "side": "LEFT",
                    "body": "Bug",
                }
            ]),
            {
                "MEOW_GITHUB_TOKEN": "secret",
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_EVENT_NAME": "pull_request",
            },
        )
    assert request.call_args.args[2]["path"] == "new.txt"
    assert request.call_args.args[2]["side"] == "LEFT"

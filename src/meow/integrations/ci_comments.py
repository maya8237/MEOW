"""Validate inline findings and publish them through the provider REST API."""

import json
import re
from codecs import escape_decode
from collections.abc import Mapping
from pathlib import Path
from typing import override
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from meow.integrations.ci_review import MAX_DIFF, CiReviewContext, CiReviewError

PAGE_SIZE = 100


class _NoRedirect(HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CiReviewError("review API redirected; check the configured API URL")


def _request(url: str, token: str, payload: dict | None = None) -> dict | list:
    if urlsplit(url).scheme != "https":
        raise CiReviewError("review API URL must use HTTPS")
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise CiReviewError(f"review API failed (HTTP {exc.code})") from None
    except (URLError, OSError, ValueError) as exc:
        raise CiReviewError("review API unavailable or returned invalid JSON") from exc


def _diff_path(value: str) -> str:
    value = value.rstrip("\t")
    if value.startswith('"'):
        value = escape_decode(value[1:-1].encode("utf-8"))[0].decode("utf-8")
    return value[2:] if value.startswith(("a/", "b/")) else value


def _locations(diff: str) -> dict:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements] -- unified diff state machine
    """Map changed-side lines to both paths, including renames and deletions."""
    locations = {}
    old_path = new_path = ""
    old_line = new_line = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk and line.startswith("--- "):
            old_path = _diff_path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            new_path = _diff_path(line[4:])
        elif match := re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line):
            old_line, new_line = map(int, match.groups())
            in_hunk = True
        elif in_hunk:
            if line.startswith("+"):
                locations[new_path, new_line, "RIGHT"] = (old_path, new_path)
                new_line += 1
            elif line.startswith("-"):
                locations[old_path, old_line, "LEFT"] = (old_path, new_path)
                old_line += 1
            elif line.startswith(" "):
                old_line += 1
                new_line += 1
    return locations


def _findings(response: str, diff: str) -> list[dict]:  # ruff: ignore[complex-structure] -- fail closed on each external JSON field
    blocks = re.findall(r"^```meow-comments\s*\n(.*?)^```\s*$", response, re.M | re.S)
    if len(blocks) != 1:
        raise CiReviewError("review must contain exactly one meow-comments JSON block")
    try:
        findings = json.loads(blocks[0])
    except ValueError as exc:
        raise CiReviewError("invalid meow-comments JSON") from exc
    if not isinstance(findings, list):
        raise CiReviewError("meow-comments must be an array")
    locations = _locations(diff)
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "path",
            "line",
            "side",
            "body",
        }:
            raise CiReviewError("inline finding has invalid fields")
        if (
            not all(isinstance(finding[key], str) for key in ("path", "side", "body"))
            or type(finding["line"]) is not int
            or not finding["body"].strip()
        ):
            raise CiReviewError("inline finding has invalid values")
        if (finding["path"], finding["line"], finding["side"]) not in locations:
            raise CiReviewError("inline finding must identify a changed diff line")
    return findings


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise CiReviewError(f"{name} is required for inline comments")
    return value


def _github_target(env: Mapping[str, str]) -> str:
    if env.get("GITHUB_EVENT_NAME") != "pull_request":
        raise CiReviewError("GitHub inline comments require a pull_request event")
    event = json.loads(Path(_required(env, "GITHUB_EVENT_PATH")).read_text())
    number = event.get("number")
    if type(number) is not int or number < 1:
        raise CiReviewError("GitHub event is missing the pull request number")
    repo = quote(_required(env, "GITHUB_REPOSITORY"), safe="/")
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    return f"{api}/repos/{repo}/pulls/{number}"


def _gitlab_target(env: Mapping[str, str]) -> str:
    project = env.get("CI_MERGE_REQUEST_PROJECT_ID") or _required(env, "CI_PROJECT_ID")
    number = _required(env, "CI_MERGE_REQUEST_IID")
    if not number.isdecimal():
        raise CiReviewError("CI_MERGE_REQUEST_IID must be a merge request number")
    api = _required(env, "CI_API_V4_URL").rstrip("/")
    return f"{api}/projects/{quote(project, safe='')}/merge_requests/{number}"


def _gitlab_position(context: CiReviewContext, finding: dict, refs: dict) -> dict:
    old_path, new_path = _locations(context.diff)[
        finding["path"], finding["line"], finding["side"]
    ]
    # GitLab expects the real path on both sides for new/deleted files.
    return {
        "position_type": "text",
        **refs,
        "old_path": new_path if old_path == "/dev/null" else old_path,
        "new_path": old_path if new_path == "/dev/null" else new_path,
        "new_line" if finding["side"] == "RIGHT" else "old_line": finding["line"],
    }


def _connection(provider: str, env: Mapping[str, str]) -> tuple[str, str]:
    if provider not in {"github", "gitlab"}:
        raise CiReviewError("inline comment provider must be github or gitlab")
    token = _required(env, f"MEOW_{provider.upper()}_TOKEN")
    target = _github_target(env) if provider == "github" else _gitlab_target(env)
    return token, target


def read_review_comments(  # ruff: ignore[complex-structure] -- bounded pagination across provider endpoints
    provider: str, env: Mapping[str, str]
) -> dict[str, list]:
    """Read every page, including replies and general review conversation."""
    token, target = _connection(provider, env)
    endpoints = {"discussions": target + "/discussions"}
    if provider == "github":
        endpoints = {
            "comments": target + "/comments",
            "reviews": target + "/reviews",
            "conversation": target.replace("/pulls/", "/issues/") + "/comments",
        }
    history = {}
    size = 0
    for kind, url in endpoints.items():
        history[kind] = []
        for page in range(1, 101):
            items = _request(f"{url}?page={page}&per_page={PAGE_SIZE}", token)
            if not isinstance(items, list) or not all(
                isinstance(item, dict) for item in items
            ):
                raise CiReviewError("review comments API returned an invalid list")
            size += len(json.dumps(items))
            if size > MAX_DIFF:
                raise CiReviewError("existing review comments exceed review size limit")
            history[kind].extend(items)
            if len(items) < PAGE_SIZE:
                break
        else:
            raise CiReviewError("review comments pagination exceeds review size limit")
    return history


def _comment_keys(  # ruff: ignore[complex-structure] -- provider-specific current-line coordinates
    provider: str, context: CiReviewContext, history: dict
) -> set:
    keys = set()
    if provider == "github":
        for comment in history["comments"]:
            # A null line denotes an outdated comment; never use original_line.
            if comment.get("line") is not None:
                keys.add((
                    comment.get("path"),
                    comment["line"],
                    comment.get("side"),
                    " ".join(comment.get("body", "").split()),
                ))
    else:
        for discussion in history["discussions"]:
            for note in discussion.get("notes", []):
                position = note.get("position") or {}
                if position.get("head_sha") != context.source_sha:
                    continue
                side = "RIGHT" if position.get("new_line") is not None else "LEFT"
                keys.add((
                    position.get("new_path" if side == "RIGHT" else "old_path"),
                    position.get("new_line" if side == "RIGHT" else "old_line"),
                    side,
                    " ".join(note.get("body", "").split()),
                ))
    return keys


def post_review_comments(  # ruff: ignore[too-many-statements] -- validate revisions and deduplicate before provider writes
    provider: str, context: CiReviewContext, response: str, env: Mapping[str, str]
) -> int:
    findings = _findings(response, context.diff)
    token, target = _connection(provider, env)
    remote = _request(target, token)
    if provider == "github":
        matches = (
            remote.get("head", {}).get("sha") == context.source_sha
            and remote.get("base", {}).get("sha") == context.target_sha
        )
        refs = {}
    else:
        refs = remote.get("diff_refs") or {}
        matches = refs == {
            "base_sha": context.merge_base,
            "start_sha": context.target_sha,
            "head_sha": context.source_sha,
        }
    if not matches:
        raise CiReviewError("remote review revisions differ from the reviewed checkout")
    existing = _comment_keys(provider, context, read_review_comments(provider, env))
    posted = 0
    for finding in findings:
        old_path, new_path = _locations(context.diff)[
            finding["path"], finding["line"], finding["side"]
        ]
        path = (
            (old_path if new_path == "/dev/null" else new_path)
            if provider == "github"
            else finding["path"]
        )
        key = (
            path,
            finding["line"],
            finding["side"],
            " ".join(finding["body"].split()),
        )
        if key in existing:
            continue
        if provider == "github":
            _request(
                target + "/comments",
                token,
                finding
                | {
                    "path": path,
                    "commit_id": context.source_sha,
                },
            )
        else:
            _request(
                target + "/discussions",
                token,
                {
                    "body": finding["body"],
                    "position": _gitlab_position(context, finding, refs),
                },
            )
        existing.add(key)
        posted += 1
    return posted

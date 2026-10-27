"""Validate inline findings and publish them through the provider REST API."""

import json
import re
from codecs import escape_decode
from collections.abc import Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import override
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from meow.integrations.ci_review import MAX_DIFF, CiReviewContext, CiReviewError

PAGE_SIZE = 100
SIMILARITY = 0.9
MARKER = "<!-- meow-review -->"


class _NoRedirect(HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CiReviewError("review API redirected; check the configured API URL")


def _request(
    url: str, token: str, payload: dict | None = None, method: str | None = None
) -> dict | list:
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
        method=method,
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


THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String){"
    "repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}"
    "nodes{id isResolved comments(first:1){nodes{databaseId body}}}}}}}"
)
RESOLVE_MUTATION = (
    "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}"
)


def _normalize(body: str) -> str:
    text = body.replace(MARKER, "").casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _similar(left: str, right: str) -> bool:
    left, right = _normalize(left), _normalize(right)
    return left == right or SequenceMatcher(None, left, right).ratio() >= SIMILARITY


def _graphql_url(env: Mapping[str, str]) -> str:
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if env.get("GITHUB_GRAPHQL_URL"):
        return env["GITHUB_GRAPHQL_URL"]
    if api.endswith("/api/v3"):
        return api.removesuffix("/v3") + "/graphql"
    return api + "/graphql"


def _github_threads(target: str, token: str, env: Mapping[str, str]) -> list[dict]:
    """Review threads as {id, resolved, root_id, body}; raises on API trouble."""
    owner, name = _required(env, "GITHUB_REPOSITORY").split("/", 1)
    variables = {"owner": owner, "name": name, "number": int(target.rsplit("/", 1)[1])}
    threads = []
    for _ in range(100):
        try:
            data = _request(
                _graphql_url(env),
                token,
                {"query": THREADS_QUERY, "variables": variables},
            )
            page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            for node in page["nodes"]:
                root = node["comments"]["nodes"][0]
                threads.append({
                    "id": node["id"],
                    "resolved": node["isResolved"],
                    "root_id": root["databaseId"],
                    "body": root["body"],
                })
            if not page["pageInfo"]["hasNextPage"]:
                return threads
            variables["after"] = page["pageInfo"]["endCursor"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CiReviewError("review threads API returned invalid data") from exc
    raise CiReviewError("review threads pagination exceeds review size limit")


def _open_github_threads(target: str, token: str, env: Mapping[str, str]) -> set:
    """Root comment ids of unresolved threads; unknown (empty) if the API fails."""
    try:
        threads = _github_threads(target, token, env)
    except CiReviewError:
        return set()
    return {t["root_id"] for t in threads if not t["resolved"]}


def _comment_keys(  # ruff: ignore[complex-structure] -- provider-specific current-line coordinates
    provider: str, context: CiReviewContext, history: dict, open_ids: set
) -> list[tuple]:
    """Existing comments as (path, line, side, body); line None = open outdated."""
    keys = []
    if provider == "github":
        for comment in history["comments"]:
            line = comment.get("line")
            # Outdated (null line) comments only count while their thread is open.
            if line is not None or comment.get("id") in open_ids:
                keys.append((
                    comment.get("path"),
                    line,
                    comment.get("side"),
                    comment.get("body", ""),
                ))
    else:
        for discussion in history["discussions"]:
            for note in discussion.get("notes", []):
                position = note.get("position") or {}
                current = position.get("head_sha") == context.source_sha
                if not current and note.get("resolved", True):
                    continue
                side = "RIGHT" if position.get("new_line") is not None else "LEFT"
                keys.append((
                    position.get("new_path" if side == "RIGHT" else "old_path"),
                    position.get("new_line" if side == "RIGHT" else "old_line")
                    if current
                    else None,
                    side,
                    note.get("body", ""),
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
    history = read_review_comments(provider, env)
    open_ids = (
        _open_github_threads(target, token, env) if provider == "github" else set()
    )
    existing = _comment_keys(provider, context, history, open_ids)
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
        if any(
            (e_path, e_side) == (path, finding["side"])
            and (e_line is None or e_line == finding["line"])
            and _similar(e_body, finding["body"])
            for e_path, e_line, e_side, e_body in existing
        ):
            continue
        if provider == "github":
            _request(
                target + "/comments",
                token,
                finding
                | {
                    "path": path,
                    "commit_id": context.source_sha,
                    "body": f"{finding['body']}\n\n{MARKER}",
                },
            )
        else:
            _request(
                target + "/discussions",
                token,
                {
                    "body": f"{finding['body']}\n\n{MARKER}",
                    "position": _gitlab_position(context, finding, refs),
                },
            )
        existing.append((path, finding["line"], finding["side"], finding["body"]))
        posted += 1
    return posted


def _valid_resolution(item: object) -> bool:
    return (
        isinstance(item, dict)
        and set(item) == {"id", "reason"}
        and isinstance(item["id"], str | int)
        and not isinstance(item["id"], bool)
        and isinstance(item["reason"], str)
        and bool(item["reason"].strip())
    )


def parse_resolutions(response: str) -> list[dict]:
    """Validate the optional meow-resolutions block; absent means none."""
    blocks = re.findall(
        r"^```meow-resolutions\s*\n(.*?)^```\s*$", response, re.M | re.S
    )
    if len(blocks) > 1:
        raise CiReviewError("review must contain at most one meow-resolutions block")
    try:
        items = json.loads(blocks[0]) if blocks else []
    except ValueError as exc:
        raise CiReviewError("invalid meow-resolutions JSON") from exc
    if not isinstance(items, list) or not all(map(_valid_resolution, items)):
        raise CiReviewError("meow-resolutions must be an array of {id, reason}")
    return items


def _open_meow_threads(
    provider: str, target: str, token: str, env: Mapping[str, str]
) -> dict[str, dict]:
    """Unresolved threads whose first comment meow authored, keyed by public id."""
    if provider == "github":
        return {
            str(t["root_id"]): t
            for t in _github_threads(target, token, env)
            if not t["resolved"] and MARKER in t["body"]
        }
    threads = {}
    for discussion in read_review_comments(provider, env)["discussions"]:
        first = (discussion.get("notes") or [{}])[0]
        if (
            MARKER in first.get("body", "")
            and first.get("resolvable")
            and not first.get("resolved")
        ):
            threads[str(discussion.get("id"))] = discussion
    return threads


def _resolve_thread(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- provider write needs connection, thread and reason
    provider: str,
    target: str,
    token: str,
    env: Mapping[str, str],
    thread: dict,
    reason: str,
) -> None:
    """Resolve the thread, or mark it solved with a check mark if that is refused."""
    base = f"{target}/discussions/{quote(str(thread['id']), safe='')}"
    try:
        if provider == "github":
            result = _request(
                _graphql_url(env),
                token,
                {"query": RESOLVE_MUTATION, "variables": {"id": thread["id"]}},
            )
            # GraphQL reports permission failures as HTTP 200 with "errors".
            if not isinstance(result, dict) or result.get("errors"):
                raise CiReviewError("thread could not be resolved")
        else:
            _request(base, token, {"resolved": True}, method="PUT")
        label = "Resolved"
    except CiReviewError:
        label = "✅ Solved"
    reply = {"body": f"{label}: {reason}"}
    if provider == "github":
        _request(f"{target}/comments/{thread['root_id']}/replies", token, reply)
    else:
        _request(base + "/notes", token, reply)


def resolve_review_comments(
    provider: str, resolutions: list[dict], env: Mapping[str, str]
) -> int:
    """Reply to and resolve open threads that meow itself started."""
    if not resolutions:
        return 0
    token, target = _connection(provider, env)
    threads = _open_meow_threads(provider, target, token, env)
    resolved = 0
    for item in resolutions:
        thread = threads.pop(str(item["id"]), None)
        if thread is not None:
            _resolve_thread(
                provider, target, token, env, thread, item["reason"].strip()
            )
            resolved += 1
    return resolved


def merge_shard_responses(responses: list[str]) -> str:
    """Join shard reports under headings with one comments and one resolutions block.

    Malformed or repeated blocks are left in place, so publishing rejects them.
    """
    pattern = re.compile(
        r"^```meow-(comments|resolutions)\s*\n(.*?)^```\s*$", re.M | re.S
    )
    merged: dict[str, list] = {"comments": [], "resolutions": []}
    seen = set()
    sections = []
    try:
        for number, response in enumerate(responses, 1):
            kinds = []
            for kind, body in pattern.findall(response):
                items = json.loads(body)
                if not isinstance(items, list):
                    raise ValueError(kind)
                merged[kind].extend(items)
                kinds.append(kind)
            if len(kinds) != len(set(kinds)):
                raise ValueError("repeated block")
            seen.update(kinds)
            body = pattern.sub("", response).strip()
            sections.append(f"## Shard {number} of {len(responses)}\n\n{body}")
    except ValueError:
        return "\n\n".join(responses)
    blocks = [
        f"```meow-{kind}\n{json.dumps(merged[kind])}\n```"
        for kind in ("comments", "resolutions")
        if kind in seen
    ]
    return "\n\n".join(sections + blocks)

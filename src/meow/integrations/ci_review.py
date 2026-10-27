"""Immutable CI checkout review with optional inline comments."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import ReviewerAgent

MAX_DIFF = 250_000
MAX_REPORT = 40_000


class CiReviewError(RuntimeError):
    """CI context or review infrastructure could not be verified."""


@dataclass(frozen=True)
class CiReviewContext:
    source_sha: str
    source_ref: str
    target_ref: str
    target_sha: str
    merge_base: str
    diff: str
    plan_text: str = ""
    mr_description: str = ""
    mr_description_truncated: bool = False
    post_comments: bool = False
    existing_comments: str = ""


@dataclass(frozen=True)
class CiReviewResult:
    exit_code: int
    verdict: str
    report_path: Path
    json_path: Path


def _git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Git stderr can contain remote URLs or credentials; report the operation only.
        raise CiReviewError(f"git {args[0]} failed (exit {result.returncode})")
    if len(result.stdout) > MAX_DIFF:
        raise CiReviewError(f"git {args[0]} output exceeds {MAX_DIFF} characters")
    return result.stdout


def _git(repo: Path, *args: str) -> str:
    return _git_bytes(repo, *args).decode("utf-8", errors="replace").strip()


def prepare_ci_review(  # ruff: ignore[complex-structure, too-many-statements, too-many-branches] -- validates each CI context requirement
    repo: Path,
    env: Mapping[str, str],
    target_ref: str = "dev",
    *,
    target_branch: str = "dev",
) -> CiReviewContext:
    source = env.get("CI_PIPELINE_SOURCE", "")
    if source == "push":
        branch = env.get("CI_COMMIT_BRANCH", "")
    elif (
        source == "merge_request_event"
        and env.get("CI_MERGE_REQUEST_EVENT_TYPE") == "detached"
    ):
        branch = env.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "")
        if env.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME") != target_branch:
            raise CiReviewError(f"CI merge request must target {target_branch}")
    else:
        raise CiReviewError("unsupported CI pipeline source or merged-result checkout")
    sha = env.get("CI_COMMIT_SHA", "")
    if not branch or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", sha):
        raise CiReviewError("CI branch or CI_COMMIT_SHA is missing or invalid")
    if not target_ref or target_ref.startswith("-"):
        raise CiReviewError("target ref is missing or invalid")
    head = _git(repo, "rev-parse", "HEAD")
    if head.lower() != sha.lower():
        raise CiReviewError("checkout HEAD does not match CI_COMMIT_SHA")
    try:
        target_sha = _git(repo, "rev-parse", "--verify", f"{target_ref}^{{commit}}")
    except CiReviewError as exc:
        raise CiReviewError(f"target ref unavailable: {target_ref}") from exc
    try:
        base = _git(repo, "merge-base", target_sha, head)
    except CiReviewError as exc:
        raise CiReviewError("target comparison history unavailable") from exc
    diff = _git(repo, "diff", "--no-ext-diff", base, head, "--")
    mr_env = env if source == "merge_request_event" else {}
    return CiReviewContext(
        head,
        branch,
        target_ref,
        target_sha,
        base,
        diff,
        mr_description=mr_env.get("CI_MERGE_REQUEST_DESCRIPTION", ""),
        mr_description_truncated=(
            mr_env.get("CI_MERGE_REQUEST_DESCRIPTION_IS_TRUNCATED") == "true"
        ),
    )


def _snapshot(repo: Path) -> tuple[str, str, str]:
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    changes = "\n".join(
        line
        for line in status.splitlines()
        if not line[3:].replace("\\", "/").startswith(".meow/ci-artifacts/")
    )
    # Porcelain records only change categories: rewriting an already modified
    # or untracked file leaves the same status. Fingerprint the contents too.
    paths = ["--", ".", ":(exclude).meow/ci-artifacts/**"]
    digest = hashlib.sha256(
        _git_bytes(
            repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", *paths
        )
    )
    digest.update(
        _git_bytes(
            repo,
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "HEAD",
            *paths,
        )
    )
    untracked = _git_bytes(
        repo, "ls-files", "--others", "--exclude-standard", "-z", *paths
    )
    for name in untracked.split(b"\0"):
        if name:
            digest.update(name)
            digest.update((repo / os.fsdecode(name)).read_bytes())
    return _git(repo, "rev-parse", "HEAD"), changes, digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _safe_text(
    value: str, env: Mapping[str, str], *, limit: int | None = MAX_REPORT
) -> str:
    for key, secret in env.items():
        if secret and (
            "TOKEN" in key or "KEY" in key or "SECRET" in key or "PASSWORD" in key
        ):
            value = value.replace(secret, "[REDACTED]")
    return value[:limit]


def _publish_comments(
    provider: str, context: CiReviewContext, text: str, env: Mapping[str, str]
) -> tuple[int | None, int | None, str | None]:
    """Publish findings; failures are reported but never change the verdict."""
    from meow.integrations.ci_comments import (
        parse_resolutions,
        post_review_comments,
        resolve_review_comments,
    )

    # A failed multi-note publish may have created some notes remotely.
    posted = resolved = None
    try:
        resolutions = parse_resolutions(text)
        posted = post_review_comments(provider, context, text, env)
        resolved = resolve_review_comments(provider, resolutions, env)
    except Exception as exc:
        return posted, resolved, _safe_text(str(exc), env)[:1000]
    return posted, resolved, None


def run_ci_review(  # ruff: ignore[complex-structure, too-many-arguments, too-many-positional-arguments, too-many-statements, too-many-branches, too-many-locals] -- public CI entry point
    repo: Path,
    config: dict,
    env: Mapping[str, str],
    target_ref: str,
    artifact_dir: Path,
    plan_file: Path | None,
    *,
    post_comments: str | None = None,
) -> CiReviewResult:
    repo = repo.resolve()
    artifact_dir = artifact_dir.resolve()
    if (
        artifact_dir.is_relative_to(repo)
        and artifact_dir != repo / ".meow" / "ci-artifacts"
    ):
        raise CiReviewError("checkout artifacts must use .meow/ci-artifacts")
    context = None
    verdict = "UNVERIFIED"
    response = ""
    reason = None
    comments_posted = 0
    comments_resolved = 0
    comments_error = None
    try:
        context = prepare_ci_review(
            repo,
            env,
            target_ref,
            target_branch=config.get("delivery", {}).get("target_branch", "dev"),
        )
        if post_comments:
            from meow.integrations.ci_comments import read_review_comments

            context = CiReviewContext(**{
                **context.__dict__,
                "post_comments": True,
                "existing_comments": _safe_text(
                    json.dumps(read_review_comments(post_comments, env)),
                    env,
                    limit=None,
                ),
            })
        before = _snapshot(repo)
        if plan_file is not None:
            if not plan_file.is_file():
                raise CiReviewError("plan file does not exist")
            plan_text = plan_file.read_text(encoding="utf-8")
            if len(plan_text) > MAX_DIFF:
                raise CiReviewError("plan file exceeds review size limit")
            context = CiReviewContext(**{**context.__dict__, "plan_text": plan_text})
        verdict, response = asyncio.run(
            ReviewerAgent(ProjectContext(repo, config)).review_ci_branch(context)
        )
        if _snapshot(repo) != before:
            raise CiReviewError("checkout HEAD or files changed during review")
        if verdict not in {"PASS", "FAIL"}:
            raise CiReviewError("reviewer returned no unambiguous verdict")
        if post_comments:
            comments_posted, comments_resolved, comments_error = _publish_comments(
                post_comments, context, _safe_text(response, env, limit=None), env
            )
    except Exception as exc:
        verdict = "UNVERIFIED"
        reason = _safe_text(str(exc), env)[:1000]
    code = 0 if verdict == "PASS" else 1 if verdict == "FAIL" else 2
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "review.md"
    json_path = artifact_dir / "verdict.json"
    payload = {
        "source_ref": context.source_ref
        if context
        else env.get("CI_COMMIT_BRANCH", ""),
        "source_sha": context.source_sha if context else env.get("CI_COMMIT_SHA", ""),
        "target_ref": target_ref,
        "target_sha": context.target_sha if context else None,
        "merge_base": context.merge_base if context else None,
        "verdict": verdict,
        "report_path": str(report_path),
        "failure_reason": reason,
        "comments_posted": comments_posted,
        "comments_resolved": comments_resolved,
        "comments_error": comments_error,
    }
    report = (
        "# CI review\n\n"
        f"Source: {payload['source_ref']} ({payload['source_sha']})\n\n"
        f"Target: {target_ref} ({payload['target_sha']})\n\n"
        f"Merge base: {payload['merge_base']}\n\n"
        f"Verdict: {verdict}\n\n"
        + (f"Failure reason: {reason}\n\n" if reason else "")
        + (
            f"Comment publishing failed (verdict unaffected): {comments_error}\n\n"
            if comments_error
            else ""
        )
        + _safe_text(response, env)
    )[:MAX_REPORT]
    _atomic_write(report_path, report)
    _atomic_write(json_path, json.dumps(payload, indent=2))
    return CiReviewResult(code, verdict, report_path, json_path)

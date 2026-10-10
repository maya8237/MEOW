"""Immutable, report-only review of a GitLab pipeline checkout."""

import asyncio
import json
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


@dataclass(frozen=True)
class CiReviewResult:
    exit_code: int
    verdict: str
    report_path: Path
    json_path: Path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        # Git stderr can contain remote URLs or credentials; report the operation only.
        raise CiReviewError(f"git {args[0]} failed (exit {result.returncode})")
    if len(result.stdout) > MAX_DIFF:
        raise CiReviewError(f"git {args[0]} output exceeds {MAX_DIFF} characters")
    return result.stdout.strip()


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


def _snapshot(repo: Path) -> tuple[str, str]:
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    changes = "\n".join(
        line
        for line in status.splitlines()
        if not line[3:].replace("\\", "/").startswith(".meow/ci-artifacts/")
    )
    return _git(repo, "rev-parse", "HEAD"), changes


def _atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _safe_text(value: str, env: Mapping[str, str]) -> str:
    for key, secret in env.items():
        if secret and (
            "TOKEN" in key or "KEY" in key or "SECRET" in key or "PASSWORD" in key
        ):
            value = value.replace(secret, "[REDACTED]")
    return value[:MAX_REPORT]


def run_ci_review(  # ruff: ignore[complex-structure, too-many-arguments, too-many-positional-arguments, too-many-statements] -- public interface prescribed by release plan
    repo: Path,
    config: dict,
    env: Mapping[str, str],
    target_ref: str,
    artifact_dir: Path,
    plan_file: Path | None,
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
    try:
        context = prepare_ci_review(
            repo,
            env,
            target_ref,
            target_branch=config.get("delivery", {}).get("target_branch", "dev"),
        )
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
    }
    report = (
        "# GitLab CI review\n\n"
        f"Source: {payload['source_ref']} ({payload['source_sha']})\n\n"
        f"Target: {target_ref} ({payload['target_sha']})\n\n"
        f"Merge base: {payload['merge_base']}\n\n"
        f"Verdict: {verdict}\n\n"
        + (f"Failure reason: {reason}\n\n" if reason else "")
        + _safe_text(response, env)
    )[:MAX_REPORT]
    _atomic_write(report_path, report)
    _atomic_write(json_path, json.dumps(payload, indent=2))
    return CiReviewResult(code, verdict, report_path, json_path)

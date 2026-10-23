"""GitHub Actions pipeline provider over the REST API."""

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from meow.integrations.ci_comments import _request
from meow.integrations.ci_review import MAX_REPORT, CiReviewError, _safe_text
from meow.integrations.pipeline_api import (
    PipelineJob,
    PipelineSnapshot,
    PipelineUnavailable,
    get_text,
)

API = "https://api.github.com"
_RUNNING = {"queued", "in_progress", "waiting", "requested", "pending"}
_CONCLUSION = {
    "success": "success",
    "failure": "failed",
    "timed_out": "failed",
    "startup_failure": "failed",
    "action_required": "failed",
    "cancelled": "canceled",
    "skipped": "skipped",
    "neutral": "skipped",
    "stale": "skipped",
}
_LINT_TIMEOUT = 60
_LINT_CAP = 50


def normalise_status(
    status: str, conclusion: str | None = None, *, job: bool = False
) -> str:
    """Map a GitHub status/conclusion to `running|success|failed|canceled|skipped`."""
    if status in _RUNNING:
        return "running"
    if status == "completed" and conclusion in _CONCLUSION:
        return _CONCLUSION[conclusion]
    # Unknown: keep waiting for a snapshot, but never block on an odd job.
    return "skipped" if job or status == "completed" else "running"


def _items(result: object, key: str) -> list:
    items = result.get(key) if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise CiReviewError("pipeline API returned an unexpected response")
    return items


class GithubPipeline:
    name = "github"

    def __init__(self, api: str, repo: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.repo = repo
        self.token = token
        self._base = f"{self.api}/repos/{repo}"

    def lint(self, repo: Path) -> list[str] | None:
        files = sorted(
            str(p.relative_to(repo))
            for pattern in ("*.yml", "*.yaml")
            for p in (repo / ".github" / "workflows").glob(pattern)
        )
        if not files or shutil.which("actionlint") is None:
            return None
        try:
            done = subprocess.run(
                ["actionlint", *files],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=_LINT_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if done.returncode == 0:
            return []
        lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
        return [_safe_text(ln, {"TOKEN": self.token}) for ln in lines[:_LINT_CAP]]

    def find_pipeline(self, sha: str) -> PipelineSnapshot | None:
        runs = _items(
            _request(
                f"{self._base}/actions/runs?head_sha={sha}&per_page=100", self.token
            ),
            "workflow_runs",
        )
        if not runs:
            return None
        newest = max(runs, key=lambda item: int(item["id"]))
        rid = newest["id"]
        raw = _items(
            _request(f"{self._base}/actions/runs/{rid}/jobs?per_page=100", self.token),
            "jobs",
        )
        jobs = tuple(self._job(item) for item in raw)
        run_status = normalise_status(
            str(newest.get("status", "")), newest.get("conclusion")
        )
        return PipelineSnapshot(
            id=str(rid),
            status=_overall(jobs, run_status),
            url=str(newest.get("html_url", "")),
            jobs=jobs,
        )

    @staticmethod
    def _job(item: dict) -> PipelineJob:
        return PipelineJob(
            id=str(item["id"]),
            name=str(item.get("name", "")),
            stage="",
            status=normalise_status(
                str(item.get("status", "")), item.get("conclusion"), job=True
            ),
            allow_failure=False,  # the API does not expose continue-on-error
            url=str(item.get("html_url", "")),
        )

    def job_log(self, job: PipelineJob) -> str:
        url = f"{self._base}/actions/jobs/{job.id}/logs"
        text = get_text(url, self.token, redirect=True)
        return _safe_text(text, {"TOKEN": self.token}, limit=MAX_REPORT)

    def review_artifact(  # ruff: ignore[no-self-use] -- protocol method
        self, job: PipelineJob, name: str
    ) -> str | None:
        """Always `None`: GitHub has no per-job artifact path; use the log."""
        return None


def _overall(jobs: tuple[PipelineJob, ...], run_status: str) -> str:
    # Any running job -> running, any failed -> failed, else success; a run
    # with no jobs yet has nothing to judge, so it keeps the run's own status.
    states = {job.status for job in jobs}
    if not jobs:
        return run_status
    if "running" in states:
        return "running"
    return "failed" if "failed" in states else "success"


def _table(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def connect_github(
    parsed: tuple[str, str], config: dict, env: Mapping[str, str]
) -> GithubPipeline:
    _host, repo = parsed
    mcp_env = _table(_table(_table(config.get("github")).get("mcp")).get("env"))
    token = (
        env.get("MEOW_GITHUB_TOKEN")
        or mcp_env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        or ""
    )
    if not token:
        raise PipelineUnavailable("MEOW_GITHUB_TOKEN is not set")
    return GithubPipeline(API, repo, token)

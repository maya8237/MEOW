"""GitLab pipeline provider over the REST API."""

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from meow.integrations.ci_comments import _request
from meow.integrations.ci_review import MAX_REPORT, CiReviewError, _safe_text
from meow.integrations.pipeline_api import (
    PipelineJob,
    PipelineSnapshot,
    PipelineUnavailable,
    get_text,
)

_RUNNING = {"pending", "created", "running", "waiting_for_resource", "preparing"}
_TERMINAL = {"success": "success", "failed": "failed", "canceled": "canceled"}


def normalise_status(status: str, *, job: bool = False) -> str:
    """Map a GitLab status to `running|success|failed|canceled|skipped`."""
    if status in _TERMINAL:
        return _TERMINAL[status]
    if status in _RUNNING:
        return "running"
    if status == "skipped" or (job and status in {"manual", "scheduled"}):
        return "skipped"
    # Unknown: keep waiting for a snapshot, but never block on an odd job.
    return "skipped" if job else "running"


class GitlabPipeline:
    name = "gitlab"

    def __init__(self, api: str, project: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.project = project
        self.token = token
        self._base = f"{self.api}/projects/{quote(project, safe='')}"

    def lint(self, repo: Path) -> list[str] | None:
        path = repo / ".gitlab-ci.yml"
        if not path.is_file():
            return None
        result = _request(
            f"{self._base}/ci/lint", self.token, {"content": path.read_text()}
        )
        if not isinstance(result, dict):
            raise CiReviewError("pipeline lint returned an unexpected response")
        if result.get("valid"):
            return []
        return [
            _safe_text(str(e), {"TOKEN": self.token}) for e in result.get("errors", [])
        ]

    def find_pipeline(self, sha: str) -> PipelineSnapshot | None:
        found = _request(
            f"{self._base}/pipelines?sha={quote(sha, safe='')}", self.token
        )
        if not isinstance(found, list) or not found:
            return None
        newest = max(found, key=lambda item: int(item["id"]))
        pid = newest["id"]
        jobs = _request(f"{self._base}/pipelines/{pid}/jobs?per_page=100", self.token)
        if not isinstance(jobs, list):
            raise CiReviewError("pipeline jobs returned an unexpected response")
        return PipelineSnapshot(
            id=str(pid),
            status=normalise_status(str(newest.get("status", ""))),
            url=str(newest.get("web_url", "")),
            jobs=tuple(self._job(item) for item in jobs),
        )

    @staticmethod
    def _job(item: dict) -> PipelineJob:
        return PipelineJob(
            id=str(item["id"]),
            name=str(item.get("name", "")),
            stage=str(item.get("stage", "")),
            status=normalise_status(str(item.get("status", "")), job=True),
            allow_failure=bool(item.get("allow_failure")),
            url=str(item.get("web_url", "")),
        )

    def job_log(self, job: PipelineJob) -> str:
        text = get_text(f"{self._base}/jobs/{job.id}/trace", self.token)
        return _safe_text(text, {"TOKEN": self.token}, limit=MAX_REPORT)

    def review_artifact(self, job: PipelineJob, name: str) -> str | None:
        url = f"{self._base}/jobs/{job.id}/artifacts/.meow/ci-artifacts/{quote(name)}"
        try:
            return get_text(url, self.token)
        except CiReviewError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise


def _table(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def connect_gitlab(
    parsed: tuple[str, str], config: dict, env: Mapping[str, str]
) -> GitlabPipeline:
    host, project = parsed
    mcp_env = _table(_table(_table(config.get("gitlab")).get("mcp")).get("env"))
    token = env.get("MEOW_GITLAB_TOKEN") or mcp_env.get("GITLAB_TOKEN") or ""
    if not token:
        raise PipelineUnavailable("MEOW_GITLAB_TOKEN is not set")
    base = str(mcp_env.get("GITLAB_URL") or f"https://{host}").rstrip("/")
    return GitlabPipeline(f"{base}/api/v4", project, token)

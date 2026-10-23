"""Provider-neutral pipeline types, plain-text fetching, and `connect`."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from meow.integrations.ci_review import CiReviewError, _safe_text

TIMEOUT = 30


class PipelineUnavailable(RuntimeError):
    """No usable pipeline provider (unsupported remote or missing credentials)."""


@dataclass(frozen=True)
class PipelineJob:
    id: str
    name: str
    stage: str
    status: str
    allow_failure: bool
    url: str


@dataclass(frozen=True)
class PipelineSnapshot:
    id: str
    status: str
    url: str
    jobs: tuple[PipelineJob, ...]


class PipelineProvider(Protocol):
    name: str

    def lint(self, repo: Path) -> list[str] | None:
        """Pipeline-file errors; `[]` valid; `None` when it could not be validated."""

    def find_pipeline(self, sha: str) -> PipelineSnapshot | None: ...

    def job_log(self, job: PipelineJob) -> str: ...

    def review_artifact(self, job: PipelineJob, name: str) -> str | None: ...


class _Redirect(Exception):
    def __init__(self, location: str) -> None:
        super().__init__("redirect")
        self.location = location


class _CaptureRedirect(HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _Redirect(newurl)


def _fetch(url: str, headers: dict[str, str]) -> str:
    if urlsplit(url).scheme != "https":
        raise CiReviewError("pipeline API URL must use HTTPS")
    request = Request(url, headers=headers)
    with build_opener(_CaptureRedirect()).open(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", "replace")


def _get(url: str, token: str, *, redirect: bool) -> str:
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/plain, */*"}
    try:
        return _fetch(url, headers)
    except _Redirect as hop:
        if not redirect:
            raise CiReviewError(
                "pipeline API redirected; check the configured API URL"
            ) from None
        return _fetch(hop.location, {"Accept": "text/plain, */*"})


def get_text(url: str, token: str, *, redirect: bool = False) -> str:
    """GET plain text; redacts `token` and caps at 40_000 chars.

    Redirects are rejected unless `redirect`, which follows exactly one and
    drops the Authorization header for it.
    """
    try:
        text = _get(url, token, redirect=redirect)
    except CiReviewError:
        raise
    except HTTPError as exc:
        raise CiReviewError(f"pipeline API failed (HTTP {exc.code})") from None
    except _Redirect:
        raise CiReviewError("pipeline API redirected more than once") from None
    except (URLError, OSError, ValueError):
        raise CiReviewError("pipeline API unavailable") from None
    return _safe_text(text, {"TOKEN": token})


def connect(remote_url: str, config: dict, env: Mapping[str, str]) -> PipelineProvider:
    """The pipeline provider for `remote_url`; raises `PipelineUnavailable`."""
    from meow.execution.orchestration.pull_request import (
        detect_provider,
        parse_remote,
    )
    from meow.integrations.pipeline_github import connect_github
    from meow.integrations.pipeline_gitlab import connect_gitlab

    provider = detect_provider(remote_url, config)
    parsed = parse_remote(remote_url)
    if provider is None or parsed is None:
        raise PipelineUnavailable(
            f"no GitHub or GitLab integration matches {remote_url!r}"
        )
    if provider == "github":
        return connect_github(parsed, config, env)
    return connect_gitlab(parsed, config, env)

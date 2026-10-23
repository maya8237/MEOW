import pytest

from meow.integrations import pipeline_api, pipeline_gitlab
from meow.integrations.ci_review import CiReviewError
from meow.integrations.pipeline_api import (
    PipelineJob,
    PipelineUnavailable,
    connect,
    get_text,
)
from meow.integrations.pipeline_gitlab import GitlabPipeline, normalise_status

API = "https://gitlab.example.com/api/v4"
BASE = f"{API}/projects/group%2Frepo"
CAP = 40_000
JOB = PipelineJob("5", "meow-review", "test", "failed", True, "u")


@pytest.fixture
def gl():
    return GitlabPipeline(API, "group/repo", "sekret")


def test_lint_posts_ci_file_content(gl, tmp_path, monkeypatch):
    calls = []
    reply = {"valid": False, "errors": ["jobs:x config contains unknown keys: scripts"]}

    def fake(url, token, payload=None):
        calls.append((url, payload))
        return reply

    monkeypatch.setattr(pipeline_gitlab, "_request", fake)
    assert gl.lint(tmp_path) is None
    assert calls == []
    (tmp_path / ".gitlab-ci.yml").write_text("x:\n  scripts: 1\n")
    assert gl.lint(tmp_path) == reply["errors"]
    assert calls == [(f"{BASE}/ci/lint", {"content": "x:\n  scripts: 1\n"})]
    reply.update(valid=True, errors=[])
    assert gl.lint(tmp_path) == []


@pytest.mark.parametrize("status", ["running", "pending", "created"])
def test_find_pipeline_by_sha(gl, monkeypatch, status):
    urls = []

    def fake(url, token, payload=None):
        urls.append(url)
        if "sha=" in url:
            return [
                {"id": 3, "status": "failed", "web_url": "old"},
                {"id": 7, "status": status, "web_url": "https://gl/p/7"},
            ]
        return [
            {
                "id": 11,
                "name": "meow-review",
                "stage": "test",
                "status": "manual",
                "allow_failure": True,
                "web_url": "https://gl/j/11",
            }
        ]

    monkeypatch.setattr(pipeline_gitlab, "_request", fake)
    snap = gl.find_pipeline("abc")
    assert urls == [
        f"{BASE}/pipelines?sha=abc",
        f"{BASE}/pipelines/7/jobs?per_page=100",
    ]
    assert (snap.id, snap.status, snap.url) == ("7", "running", "https://gl/p/7")
    assert snap.jobs == (
        PipelineJob("11", "meow-review", "test", "skipped", True, "https://gl/j/11"),
    )


def test_find_pipeline_none_when_absent(gl, monkeypatch):
    monkeypatch.setattr(pipeline_gitlab, "_request", lambda *a, **k: [])
    assert gl.find_pipeline("abc") is None


def test_job_log_is_redacted_and_capped(gl, monkeypatch):
    seen = []

    def fake(url, token, **kw):
        seen.append(url)
        return "token=sekret " + "x" * 50_000

    monkeypatch.setattr(pipeline_gitlab, "get_text", fake)
    log = gl.job_log(JOB)
    assert seen == [f"{BASE}/jobs/5/trace"]
    assert "sekret" not in log
    assert log.startswith("token=[REDACTED]")
    assert len(log) <= CAP


def test_review_artifact_path_and_none_on_404(gl, monkeypatch):
    seen = []

    def ok(url, token, **kw):
        seen.append(url)
        return "{}"

    monkeypatch.setattr(pipeline_gitlab, "get_text", ok)
    assert gl.review_artifact(JOB, "verdict.json") == "{}"
    assert seen == [f"{BASE}/jobs/5/artifacts/.meow/ci-artifacts/verdict.json"]

    def missing(url, token, **kw):
        raise CiReviewError("review API failed (HTTP 404)")

    monkeypatch.setattr(pipeline_gitlab, "get_text", missing)
    assert gl.review_artifact(JOB, "verdict.json") is None

    def boom(url, token, **kw):
        raise CiReviewError("pipeline API failed (HTTP 500)")

    monkeypatch.setattr(pipeline_gitlab, "get_text", boom)
    with pytest.raises(CiReviewError):
        gl.review_artifact(JOB, "verdict.json")


@pytest.mark.parametrize(
    ("raw", "job", "expected"),
    [
        ("pending", False, "running"),
        ("created", True, "running"),
        ("waiting_for_resource", True, "running"),
        ("preparing", False, "running"),
        ("manual", True, "skipped"),
        ("scheduled", True, "skipped"),
        ("success", True, "success"),
        ("failed", False, "failed"),
        ("canceled", True, "canceled"),
        ("skipped", False, "skipped"),
        ("weird", False, "running"),
        ("weird", True, "skipped"),
    ],
)
def test_normalise_status(raw, job, expected):
    assert normalise_status(raw, job=job) == expected


def test_connect_requires_token():
    with pytest.raises(PipelineUnavailable, match="MEOW_GITLAB_TOKEN"):
        connect("git@gitlab.com:group/repo.git", {}, {})


def test_connect_token_and_api_sources():
    p = connect("git@gitlab.com:group/repo.git", {}, {"MEOW_GITLAB_TOKEN": "a"})
    assert (p.api, p.project, p.token) == (
        "https://gitlab.com/api/v4",
        "group/repo",
        "a",
    )
    cfg = {
        "gitlab": {
            "mcp": {"env": {"GITLAB_TOKEN": "b", "GITLAB_URL": "https://ci.corp/"}}
        }
    }
    p = connect("https://ci.corp/g/r.git", cfg, {})
    assert (p.api, p.token) == ("https://ci.corp/api/v4", "b")


def test_connect_unmatched_and_github():
    with pytest.raises(PipelineUnavailable, match="no GitHub or GitLab"):
        connect("/local/path", {}, {})
    with pytest.raises(PipelineUnavailable, match="GitHub pipeline support"):
        connect("git@github.com:o/r.git", {}, {"MEOW_GITHUB_TOKEN": "t"})


def test_get_text_rejects_non_https():
    with pytest.raises(CiReviewError, match="HTTPS"):
        get_text("http://x.example/trace", "t")


def test_get_text_rejects_redirect_by_default(monkeypatch):
    def fake(url, headers):
        raise pipeline_api._Redirect("https://cdn/x")

    monkeypatch.setattr(pipeline_api, "_fetch", fake)
    with pytest.raises(CiReviewError, match="redirected") as err:
        get_text("https://api/x?sig=s3", "tok")
    assert "s3" not in str(err.value)


def test_get_text_follows_one_redirect_without_auth(monkeypatch):
    calls = []

    def fake(url, headers):
        calls.append((url, dict(headers)))
        if len(calls) == 1:
            raise pipeline_api._Redirect("https://cdn/log")
        return "hello tok"

    monkeypatch.setattr(pipeline_api, "_fetch", fake)
    assert get_text("https://api/x", "tok", redirect=True) == "hello [REDACTED]"
    assert "Authorization" in calls[0][1]
    assert calls[1][0] == "https://cdn/log"
    assert "Authorization" not in calls[1][1]


def test_get_text_rejects_second_redirect(monkeypatch):
    def fake(url, headers):
        raise pipeline_api._Redirect("https://cdn/again")

    monkeypatch.setattr(pipeline_api, "_fetch", fake)
    with pytest.raises(CiReviewError, match="more than once"):
        get_text("https://api/x", "t", redirect=True)


def test_get_text_caps_and_redacts(monkeypatch):
    monkeypatch.setattr(pipeline_api, "_fetch", lambda u, h: "tok" + "y" * 60_000)
    out = get_text("https://api/x", "tok")
    assert out.startswith("[REDACTED]") and len(out) == CAP


def test_capture_redirect_handler_raises():
    handler = pipeline_api._CaptureRedirect()
    with pytest.raises(pipeline_api._Redirect) as hop:
        handler.redirect_request(None, None, 302, "", {}, "https://cdn/z")
    assert hop.value.location == "https://cdn/z"

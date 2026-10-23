import subprocess

import pytest

from meow.integrations import pipeline_github
from meow.integrations.pipeline_api import (
    PipelineJob,
    PipelineUnavailable,
    connect,
)
from meow.integrations.pipeline_github import GithubPipeline, normalise_status

API = "https://api.github.com"
BASE = f"{API}/repos/o/r"
CAP, TIMEOUT, ARGS = 50, 60, 3
JOB = PipelineJob("5", "meow-review", "", "failed", False, "u")


@pytest.fixture
def gh():
    return GithubPipeline(API, "o/r", "sekret")


def _fake_api(runs, jobs, urls):
    def fake(url, token, payload=None):
        urls.append(url)
        return {"workflow_runs": runs} if "head_sha=" in url else {"jobs": jobs}

    return fake


def test_find_pipeline_picks_highest_run_id(gh, monkeypatch):
    urls = []
    runs = [
        {"id": 3, "status": "completed", "conclusion": "failure", "html_url": "old"},
        {"id": 9, "status": "in_progress", "conclusion": None, "html_url": "R9"},
    ]
    jobs = [
        {"id": 11, "name": "a", "status": "completed", "conclusion": "success"},
        {
            "id": 12,
            "name": "meow-review",
            "status": "queued",
            "conclusion": None,
            "html_url": "J12",
        },
    ]
    monkeypatch.setattr(pipeline_github, "_request", _fake_api(runs, jobs, urls))
    snap = gh.find_pipeline("abc")
    assert urls == [
        f"{BASE}/actions/runs?head_sha=abc&per_page=100",
        f"{BASE}/actions/runs/9/jobs?per_page=100",
    ]
    assert (snap.id, snap.status, snap.url) == ("9", "running", "R9")
    assert snap.jobs[1] == PipelineJob("12", "meow-review", "", "running", False, "J12")
    assert snap.jobs[0].status == "success"


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["failure", "success"], "failed"),
        (["success", "skipped"], "success"),
        (["cancelled"], "success"),
    ],
)
def test_snapshot_status_from_jobs(gh, monkeypatch, states, expected):
    runs = [{"id": 1, "status": "completed", "conclusion": "x", "html_url": "u"}]
    jobs = [
        {"id": i, "name": f"j{i}", "status": "completed", "conclusion": c}
        for i, c in enumerate(states)
    ]
    monkeypatch.setattr(pipeline_github, "_request", _fake_api(runs, jobs, []))
    assert gh.find_pipeline("abc").status == expected


def test_find_pipeline_none_when_no_runs(gh, monkeypatch):
    monkeypatch.setattr(
        pipeline_github, "_request", lambda *a, **k: {"workflow_runs": []}
    )
    assert gh.find_pipeline("abc") is None


@pytest.mark.parametrize(
    ("status", "conclusion", "job", "expected"),
    [
        ("queued", None, True, "running"),
        ("in_progress", None, False, "running"),
        ("waiting", None, True, "running"),
        ("requested", None, True, "running"),
        ("pending", None, False, "running"),
        ("completed", "success", True, "success"),
        ("completed", "failure", True, "failed"),
        ("completed", "timed_out", True, "failed"),
        ("completed", "startup_failure", False, "failed"),
        ("completed", "action_required", True, "failed"),
        ("completed", "cancelled", True, "canceled"),
        ("completed", "skipped", True, "skipped"),
        ("completed", "neutral", False, "skipped"),
        ("completed", "stale", True, "skipped"),
        ("completed", "weird", True, "skipped"),
        ("weird", None, False, "running"),
        ("weird", None, True, "skipped"),
    ],
)
def test_normalise_status(status, conclusion, job, expected):
    assert normalise_status(status, conclusion, job=job) == expected


def _workflow(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\n")
    (wf / "b.yaml").write_text("on: push\n")
    return wf


def test_lint_uses_actionlint(gh, tmp_path, monkeypatch):
    _workflow(tmp_path)
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 1, "a.yml:3:1: bad key\n\n", "")

    monkeypatch.setattr(pipeline_github.shutil, "which", lambda n: "/bin/actionlint")
    monkeypatch.setattr(pipeline_github.subprocess, "run", run)
    assert gh.lint(tmp_path) == ["a.yml:3:1: bad key"]
    cmd, kw = calls[0]
    assert cmd[0] == "actionlint" and len(cmd) == ARGS
    assert kw["cwd"] == tmp_path and kw["timeout"] == TIMEOUT and kw["capture_output"]
    assert not kw.get("shell")


def test_lint_clean_and_capped(gh, tmp_path, monkeypatch):
    _workflow(tmp_path)
    monkeypatch.setattr(pipeline_github.shutil, "which", lambda n: "/bin/actionlint")
    monkeypatch.setattr(
        pipeline_github.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    assert gh.lint(tmp_path) == []
    out = "\n".join(f"e{i}" for i in range(80))
    monkeypatch.setattr(
        pipeline_github.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, out, ""),
    )
    assert len(gh.lint(tmp_path)) == CAP


def test_lint_none_cases(gh, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_github.shutil, "which", lambda n: None)
    _workflow(tmp_path)
    assert gh.lint(tmp_path) is None
    monkeypatch.setattr(pipeline_github.shutil, "which", lambda n: "/bin/actionlint")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(pipeline_github.subprocess, "run", timeout)
    assert gh.lint(tmp_path) is None

    def oserr(cmd, **kw):
        raise OSError("nope")

    monkeypatch.setattr(pipeline_github.subprocess, "run", oserr)
    assert gh.lint(tmp_path) is None


def test_lint_none_without_workflow_files(gh, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_github.shutil, "which", lambda n: "/bin/actionlint")
    assert gh.lint(tmp_path) is None


def test_job_log_follows_redirect_and_redacts(gh, monkeypatch):
    seen = []

    def fake(url, token, **kw):
        seen.append((url, token, kw))
        return "ok"

    monkeypatch.setattr(pipeline_github, "get_text", fake)
    assert gh.job_log(JOB) == "ok"
    assert seen == [(f"{BASE}/actions/jobs/5/logs", "sekret", {"redirect": True})]
    monkeypatch.setattr(pipeline_github, "get_text", lambda *a, **k: "a sekret b")
    assert "sekret" not in gh.job_log(JOB)


def test_review_artifact_is_none(gh):
    assert gh.review_artifact(JOB, "verdict.json") is None


def test_connect_requires_token():
    with pytest.raises(PipelineUnavailable, match="MEOW_GITHUB_TOKEN is not set"):
        connect("git@github.com:o/r.git", {}, {})


def test_connect_token_sources():
    p = connect("git@github.com:o/r.git", {}, {"MEOW_GITHUB_TOKEN": "a"})
    assert isinstance(p, GithubPipeline)
    assert (p.api, p.repo, p.token) == (API, "o/r", "a")
    cfg = {"github": {"mcp": {"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "b"}}}}
    assert connect("https://github.com/o/r.git", cfg, {}).token == "b"

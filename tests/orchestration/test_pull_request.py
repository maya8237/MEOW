"""Provider detection, manual links, titles, and idempotent PR opening."""

import asyncio
from types import SimpleNamespace

import pytest

from meow.execution.orchestration import pull_request as pull_request_module
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.pull_request import (
    detect_provider,
    manual_pr_link,
    open_pull_request,
    pull_request_title,
)
from meow.execution.orchestration.report import EXIT_DELIVERED_WITHOUT_PR, exit_code
from tests.orchestration.fakes import (
    create_orchestrator,
    make_repository,
    orchestrator_config,
)
from tests.orchestration.review_fakes import ScriptedPullRequest

GITHUB_MCP = {"github": {"mcp": {"command": "x"}}}
GITLAB_MCP = {"gitlab": {"mcp": {"command": "x"}}}
SELF_HOSTED = {
    "gitlab": {"mcp": {"command": "x", "env": {"GITLAB_URL": "https://code.corp"}}}
}


@pytest.mark.parametrize(
    ("url", "config", "expected"),
    [
        ("git@github.com:o/r.git", GITHUB_MCP, "github"),
        ("https://github.com/o/r.git", {}, "github"),
        ("ssh://git@github.example.com/o/r.git", {}, "github"),
        ("https://gitlab.example.com/g/r.git", GITLAB_MCP, "gitlab"),
        ("https://code.corp/g/r.git", SELF_HOSTED, "gitlab"),
        ("https://code.corp/g/r.git", {}, None),
        ("/tmp/remote.git", {}, None),
        ("file:///tmp/remote.git", {}, None),
        ("C:\\work\\remote.git", {}, None),
    ],
)
def test_detect_provider(url, config, expected):
    assert detect_provider(url, config) == expected


def test_manual_link_for_github_and_gitlab():
    assert manual_pr_link(
        "git@github.com:o/r.git", "github", "main", "orchestrator/abc"
    ) == ("https://github.com/o/r/compare/main...orchestrator/abc?expand=1")
    assert manual_pr_link(
        "https://gitlab.example.com/g/sub/r.git", "gitlab", "main", "orchestrator/abc"
    ) == (
        "https://gitlab.example.com/g/sub/r/-/merge_requests/new"
        "?merge_request[source_branch]=orchestrator/abc"
        "&merge_request[target_branch]=main"
    )


def test_no_manual_link_without_a_provider_or_a_network_remote():
    assert manual_pr_link("git@github.com:o/r.git", None, "main", "b") is None
    assert manual_pr_link("/tmp/remote.git", "github", "main", "b") is None


def record_for(tmp_path, **changes):
    repo, _remote = make_repository(tmp_path)
    store, oid = create_orchestrator(
        repo,
        [
            OrchestratorTask("PROJ-1", "one", source_ref="PROJ-1", status="merged"),
            OrchestratorTask("PROJ-2", "two", source_ref="PROJ-2", status="merged"),
        ],
    )
    store.update(
        oid, lambda record: [setattr(record, k, v) for k, v in changes.items()]
    )
    return repo, store, oid


def test_title_for_an_objective_uses_its_first_line(tmp_path):
    _repo, store, oid = record_for(tmp_path, objective="Add login\nwith SSO")

    assert pull_request_title(store.load(oid)) == "Orchestrator: Add login"


@pytest.mark.parametrize("source", ["jira", "jql"])
def test_title_for_jira_sources_lists_the_keys(tmp_path, source):
    _repo, store, oid = record_for(tmp_path, source=source)

    assert pull_request_title(store.load(oid)) == "Orchestrator: PROJ-1, PROJ-2"


def test_long_titles_are_cut(tmp_path):
    _repo, store, oid = record_for(tmp_path, objective="x" * 500)

    assert len(pull_request_title(store.load(oid))) == len("Orchestrator: ") + 200


def with_origin(monkeypatch, origin):
    """Make `origin` the URL the pull request code sees for the remote."""
    monkeypatch.setattr(pull_request_module, "origin_url", lambda repo: origin)


def run_open(tmp_path, stub, config_extra, **changes):
    repo, store, oid = record_for(tmp_path, objective="Add login", **changes)
    config = {**orchestrator_config(repo), **config_extra}
    agents = SimpleNamespace(pull_request=stub.factory)
    asyncio.run(open_pull_request(repo, store, oid, config, agents))
    return repo, store, oid


@pytest.mark.parametrize("error", [None, "rate limited"])
def test_saved_target_is_used_for_opening_and_manual_links(
    tmp_path, monkeypatch, error
):
    stub = ScriptedPullRequest(url="https://github.com/o/r/pull/7", error=error)
    with_origin(monkeypatch, "git@github.com:o/r.git")
    _repo, store, oid = run_open(
        tmp_path, stub, GITHUB_MCP, options={"target_branch": "dev"}
    )

    assert store.load(oid).base_branch == "main"
    assert stub.calls[0][3] == "dev"
    if error:
        assert store.load(oid).delivery["pr_manual_link"] == (
            f"https://github.com/o/r/compare/dev...orchestrator/{oid}?expand=1"
        )


def test_stub_agent_success_records_url(tmp_path, monkeypatch):
    stub = ScriptedPullRequest(url="https://github.com/o/r/pull/7")
    with_origin(monkeypatch, "git@github.com:o/r.git")

    _repo, store, oid = run_open(tmp_path, stub, GITHUB_MCP)

    record = store.load(oid)
    assert record.delivery["pr"] == {
        "url": "https://github.com/o/r/pull/7",
        "provider": "github",
    }
    assert record.delivery["pr_error"] is None
    assert record.delivery["pr_manual_link"] is None
    title, body, head, base, output = stub.calls[0]
    assert (title, head, base) == (
        "Orchestrator: Add login",
        f"orchestrator/{oid}",
        "main",
    )
    assert body.startswith(f"# Orchestrator {oid}")
    assert output == store.dir_for(oid) / "pull-request.json"
    assert stub.built[0][1:] == (
        {"command": "x", "args": [], "env": {}},
        "github",
    )


def test_agent_failure_records_error_and_keeps_delivered(tmp_path, monkeypatch):
    stub = ScriptedPullRequest(error="rate limited")
    with_origin(monkeypatch, "git@github.com:o/r.git")

    _repo, store, oid = run_open(tmp_path, stub, GITHUB_MCP)

    store.update(oid, lambda record: setattr(record, "phase", "delivered"))
    record = store.load(oid)
    assert record.delivery["pr"] is None
    assert record.delivery["pr_error"] == "rate limited"
    assert record.delivery["pr_manual_link"] == (
        f"https://github.com/o/r/compare/main...orchestrator/{oid}?expand=1"
    )
    assert exit_code(record) == EXIT_DELIVERED_WITHOUT_PR


def test_existing_pr_is_reused(tmp_path, monkeypatch):
    stub = ScriptedPullRequest(url="https://github.com/o/r/pull/8")
    repo, store, oid = record_for(tmp_path)
    with_origin(monkeypatch, "git@github.com:o/r.git")
    existing = {"url": "https://github.com/o/r/pull/1", "provider": "github"}
    store.update(oid, lambda record: record.delivery.update(pr=existing))
    config = {**orchestrator_config(repo), **GITHUB_MCP}

    asyncio.run(
        open_pull_request(
            repo, store, oid, config, SimpleNamespace(pull_request=stub.factory)
        )
    )

    assert stub.calls == []
    assert store.load(oid).delivery["pr"] == existing


def test_unconfigured_provider_is_reported_with_a_manual_link(tmp_path, monkeypatch):
    stub = ScriptedPullRequest(url="unused")
    with_origin(monkeypatch, "git@github.com:o/r.git")

    _repo, store, oid = run_open(tmp_path, stub, {})

    record = store.load(oid)
    assert record.delivery["pr_error"].startswith("provider not configured:")
    assert record.delivery["pr_manual_link"].startswith(
        "https://github.com/o/r/compare/"
    )
    assert stub.calls == []


def test_local_origin_has_no_provider_and_no_link(tmp_path, monkeypatch):
    stub = ScriptedPullRequest(url="unused")
    with_origin(monkeypatch, str(tmp_path / "remote.git"))

    _repo, store, oid = run_open(tmp_path, stub, GITHUB_MCP)

    record = store.load(oid)
    assert "no GitHub or GitLab integration matches" in record.delivery["pr_error"]
    assert record.delivery["pr_manual_link"] is None
    assert stub.calls == []

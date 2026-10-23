"""Opening the merge request for a plain run, with a fake pull-request agent."""

import asyncio

import pytest

from meow.execution import final_delivery
from meow.execution.final_delivery import open_run_merge_request
from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled

GITLAB = "git@gitlab.com:team/app.git"
GITHUB = "https://github.com/team/app.git"
CONFIG = {
    "gitlab": {"mcp": {"command": "x"}, "url": "https://gitlab.com", "token": "t"},
    "github": {"mcp": {"command": "x"}},
}


class FakeAgent:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple] = []

    async def open(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- mirrors the agent
        self, title, body, head, base, output_file
    ):
        self.calls.append((title, body, head, base, output_file))
        if self.error:
            raise self.error
        return "https://gitlab.com/team/app/-/merge_requests/7"


class FakeAgents:
    def __init__(self, agent: FakeAgent):
        self.agent = agent
        self.built: list[tuple] = []

    def pull_request(self, context, mcp, provider):
        self.built.append((context, mcp, provider))
        return self.agent


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(final_delivery, "origin_url", lambda repo: GITLAB)
    monkeypatch.setattr(final_delivery, "provider_mcp", lambda c, p: {"command": "x"})

    def make(delivery=None, request="Add login\nwith details"):
        store = RunStore(tmp_path)
        delivery = {
            "base": "main",
            "commit": "abc",
            "branch": "meow/x",
            **(delivery or {}),
        }
        record = store.create(
            source="run",
            request=request,
            repo=tmp_path,
            worktree=tmp_path,
            branch="meow/x",
            delivery=delivery,
        )
        return store, record.id

    return make


def run(store, run_id, agent, config=CONFIG):
    return asyncio.run(open_run_merge_request(store, run_id, config, FakeAgents(agent)))


def test_opens_mr_into_from_branch(setup, tmp_path):
    store, rid = setup()
    agent = FakeAgent()
    result = run(store, rid, agent)
    title, body, head, base, out = agent.calls[0]
    assert (head, base) == ("meow/x", "main")
    assert title == "MEOW: Add login"
    assert rid in body and "with details" in body
    assert "Local review and checks passed before delivery." in body
    assert out == store.directory / f"{rid}.pull-request.json"
    assert result == {
        "url": "https://gitlab.com/team/app/-/merge_requests/7",
        "provider": "gitlab",
    }
    delivery = store.load(rid).delivery
    assert delivery["pr"]["url"].endswith("/merge_requests/7")
    assert delivery["pr_error"] is None and delivery["pr_manual_link"] is None


def test_title_truncated_and_plan_listed(setup):
    store, rid = setup(request="x" * 200)
    store.transition(rid, "created", plan_file="plans/p.md")
    agent = FakeAgent()
    run(store, rid, agent)
    assert agent.calls[0][0] == "MEOW: " + "x" * 72
    assert "plans/p.md" in agent.calls[0][1]


def test_delivery_keys_preserved(setup):
    store, rid = setup({"pipeline": {"rounds": 0}})
    run(store, rid, FakeAgent())
    delivery = store.load(rid).delivery
    assert delivery["commit"] == "abc" and delivery["pipeline"] == {"rounds": 0}
    assert delivery["base"] == "main"


def test_second_call_does_not_reopen(setup):
    store, rid = setup()
    agent = FakeAgent()
    first = run(store, rid, agent)
    second = run(store, rid, agent)
    assert len(agent.calls) == 1
    assert second == first


def test_provider_failure_records_manual_link(setup):
    store, rid = setup()
    assert run(store, rid, FakeAgent(RuntimeError("boom"))) is None
    delivery = store.load(rid).delivery
    assert delivery["pr"] is None and delivery["pr_error"] == "boom"
    assert delivery["pr_manual_link"].endswith("[target_branch]=main")
    assert delivery["commit"] == "abc"


def test_github_manual_link(setup, monkeypatch):
    monkeypatch.setattr(final_delivery, "origin_url", lambda repo: GITHUB)
    store, rid = setup()
    run(store, rid, FakeAgent(ValueError()))
    delivery = store.load(rid).delivery
    assert delivery["pr_error"] == "ValueError"
    assert "compare/main...meow/x" in delivery["pr_manual_link"]


def test_unknown_host_records_reason(setup, monkeypatch):
    monkeypatch.setattr(
        final_delivery, "origin_url", lambda repo: "git@example.org:a/b.git"
    )
    store, rid = setup()
    agent = FakeAgent()
    assert run(store, rid, agent) is None
    delivery = store.load(rid).delivery
    assert "example.org" in delivery["pr_error"] and "\n" not in delivery["pr_error"]
    assert delivery["pr_manual_link"] is None and not agent.calls


def test_missing_origin_records_reason(setup, monkeypatch):
    monkeypatch.setattr(final_delivery, "origin_url", lambda repo: None)
    store, rid = setup()
    assert run(store, rid, FakeAgent()) is None
    assert "origin" in store.load(rid).delivery["pr_error"]


def test_missing_provider_config_records_reason(setup, monkeypatch):
    def missing(config, provider):
        raise RuntimeError("provider not configured: no token")

    monkeypatch.setattr(final_delivery, "provider_mcp", missing)
    store, rid = setup()
    assert run(store, rid, FakeAgent()) is None
    assert "not configured" in store.load(rid).delivery["pr_error"]


def test_missing_base_branch(setup):
    store, rid = setup()
    record = store.load(rid)
    delivery = {k: v for k, v in record.delivery.items() if k != "base"}
    store.transition(rid, record.phase, delivery=delivery)
    agent = FakeAgent()
    assert run(store, rid, agent) is None
    assert store.load(rid).delivery["pr_error"] == "no base branch recorded"
    assert not agent.calls


def test_run_cancelled_propagates(setup):
    store, rid = setup()
    with pytest.raises(RunCancelled):
        run(store, rid, FakeAgent(RunCancelled("stop")))


def test_token_text_is_redacted(setup):
    store, rid = setup()
    run(store, rid, FakeAgent(RuntimeError("bad\nGITLAB_TOKEN=secret and API_KEY: s3")))
    error = store.load(rid).delivery["pr_error"]
    assert "secret" not in error and "s3" not in error
    assert "\n" not in error and "GITLAB_TOKEN" in error


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("GITLAB_TOKEN=abc123", "abc123"),
        ('{"token": "abc123"}', "abc123"),
        ('{"password": "abc123"}', "abc123"),
        ("Authorization: Bearer abc123", "abc123"),
        ("token: Bearer abc123", "abc123"),
        ("PRIVATE-TOKEN: abc123", "abc123"),
        ("JOB-TOKEN=abc123", "abc123"),
        ("failed at https://oauth2:abc123@host/g/r.git now", "abc123"),
    ],
)
def test_credential_forms_scrubbed(setup, text, secret):
    store, rid = setup()
    run(store, rid, FakeAgent(RuntimeError(f"bad\n{text}")))
    delivery = store.load(rid).delivery
    assert secret not in delivery["pr_error"]
    assert secret not in str(delivery["pr_manual_link"])
    assert "\n" not in delivery["pr_error"]


def test_remote_userinfo_never_persisted(setup, monkeypatch):
    remote = "https://oauth2:glpat-secret@gitlab.com/team/app.git"
    monkeypatch.setattr(final_delivery, "origin_url", lambda repo: remote)
    store, rid = setup()
    run(store, rid, FakeAgent(RuntimeError(f"cannot reach {remote}")))
    delivery = store.load(rid).delivery
    assert "glpat-secret" not in delivery["pr_error"]
    assert "glpat-secret" not in delivery["pr_manual_link"]
    assert "gitlab.com/team/app" in delivery["pr_manual_link"]


def test_unknown_host_remote_userinfo_scrubbed(setup, monkeypatch):
    remote = "https://oauth2:glpat-secret@example.org/a/b.git"
    monkeypatch.setattr(final_delivery, "origin_url", lambda repo: remote)
    store, rid = setup()
    run(store, rid, FakeAgent())
    delivery = store.load(rid).delivery
    assert "glpat-secret" not in delivery["pr_error"]
    assert "example.org" in delivery["pr_error"]

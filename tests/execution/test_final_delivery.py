"""Final delivery of a plain run: the MR, then pipeline verification (fakes)."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from meow.execution import final_delivery
from meow.execution.final_delivery import open_run_merge_request, run_final_delivery
from meow.execution.pipeline_verify import PipelineOutcome
from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled
from meow.integrations.pipeline_api import (
    PipelineJob,
    PipelineSnapshot,
    PipelineUnavailable,
)

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


# --- run_final_delivery: the stage after `deliver` pushed the run branch ---

PIPELINE = {
    "open_mr": True,
    "max_fix_rounds": 3,
    "start_timeout": 300,
    "timeout": 3600,
    "poll_seconds": 15,
    "fix_non_meow_jobs": False,
}
PIPE_URL = "https://gitlab.com/team/app/-/pipelines/9"
MR_URL = "https://gitlab.com/team/app/-/merge_requests/7"


@dataclass
class FakeProvider:
    """A green pipeline whose `meow-review` job passed."""

    name: str = "gitlab"
    lint_errors: list = field(default_factory=list)
    shas: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)

    def lint(self, repo):
        return list(self.lint_errors)

    def find_pipeline(self, sha):
        self.shas.append(sha)
        job = PipelineJob("1", "meow-review", "review", "success", False, "u")
        return PipelineSnapshot("9", "success", PIPE_URL, (job,))

    def job_log(self, job):
        return self.artifacts.get(job.name, "")

    def review_artifact(self, job, name):
        return self.artifacts.get(name)


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """Build a delivered run and a workflow state; fakes for MR and pipeline."""
    agent = FakeAgent()
    calls = SimpleNamespace(connect=[], verify=[], agent=agent)
    monkeypatch.setattr(final_delivery, "origin_url", lambda repo: GITLAB)
    monkeypatch.setattr(final_delivery, "provider_mcp", lambda c, p: {"command": "x"})
    monkeypatch.setattr(final_delivery, "OrchestratorAgents", lambda: FakeAgents(agent))

    def connect(remote, config, env):
        calls.connect.append(remote)
        return FakeProvider()

    monkeypatch.setattr(final_delivery, "connect", connect)

    def make(delivery=None, options=None):
        store = RunStore(tmp_path)
        delivery = {
            "base": "main",
            "push": True,
            "pushed": True,
            "commit": "abcdef1234",
            "branch": "meow/x",
            **(delivery or {}),
        }
        record = store.create(
            source="prompt",
            request="Add login",
            repo=tmp_path,
            worktree=tmp_path,
            branch="meow/x",
        )
        store.transition(record.id, "delivered", delivery=delivery)
        config = {**CONFIG, "pipeline": {**PIPELINE, **(options or {})}}
        sprint = SimpleNamespace(config=config)
        return SimpleNamespace(store=store, run_id=record.id, sprint=sprint)

    calls.make = make
    return calls


def fake_verify(monkeypatch, calls, outcome):
    async def verify(*args):
        store, run_id, provider, fixers, settings, announce = args
        await asyncio.sleep(0)
        calls.verify.append((provider, fixers, settings))
        announce("Pipeline running on abcdef1 (fix round 1 of 3).")
        store.transition(run_id, "pipeline_waiting")
        return outcome

    monkeypatch.setattr(final_delivery, "verify_pipeline", verify)


def go(state):
    return asyncio.run(run_final_delivery(state))


def test_noop_without_base(stage, capsys):
    state = stage.make({"base": None})
    assert go(state) is None
    assert not stage.connect and not stage.agent.calls
    assert capsys.readouterr().out == ""


def test_noop_when_not_pushing(stage, capsys):
    state = stage.make({"push": False})
    assert go(state) is None
    assert capsys.readouterr().out == ""


def test_open_mr_false_skips_everything(stage, capsys):
    state = stage.make({"pipeline_options": {"open_mr": False}})
    assert go(state) is None
    assert not stage.connect and not stage.agent.calls
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("[delivery] ")
    assert "skipped" in out and "--no-pr" in out
    assert "Verified by pipeline" not in out


def test_prints_stage_lines_in_order(stage, capsys):
    state = stage.make()
    outcome = go(state)
    assert outcome.status == "verified"
    lines = capsys.readouterr().out.splitlines()
    assert all(line.startswith("[delivery] ") for line in lines)
    texts = [line.removeprefix("[delivery] ") for line in lines]
    assert texts[0] == (
        "Feature complete. MEOW loop finished; entering final delivery (MR into main)."
    )
    assert texts[1] == (
        f"MR opened: {MR_URL}. Waiting for the pipeline to verify the delivery."
    )
    assert texts[2] == "Pipeline running on abcdef1 (fix round 1 of 3)."
    assert texts[-1] == f"Verified by pipeline: {PIPE_URL}"
    delivery = state.store.load(state.run_id).delivery
    assert delivery["stage"] == texts[-1]
    assert delivery["pipeline"]["status"] == "verified"
    assert delivery["pipeline"]["url"] == PIPE_URL
    assert stage.connect == [GITLAB]


@pytest.mark.parametrize(
    "case",
    [
        ("no_pipeline", "no pipeline started for abcdef1"),
        ("not_verified", "pipeline has no jobs"),
        ("unverified", "auth failed"),
    ],
)
def test_never_prints_verified_when_not_verified(stage, monkeypatch, capsys, case):
    status, detail = case
    fake_verify(monkeypatch, stage, PipelineOutcome(status, 0, "", (), detail))
    state = stage.make()
    assert go(state).status == status
    out = capsys.readouterr().out
    assert f"Not verified by pipeline: {detail}" in out
    assert "Verified by pipeline" not in out
    assert state.store.load(state.run_id).delivery["pipeline"]["status"] == status


def test_unvalidated_is_qualified(stage, monkeypatch, capsys):
    outcome = PipelineOutcome("unvalidated", 0, PIPE_URL, (), "")
    fake_verify(monkeypatch, stage, outcome)
    go(stage.make())
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith(
        "[delivery] Verified by pipeline (pipeline file not validated"
    )
    assert last.endswith(PIPE_URL)


def test_exhausted_lists_failing_jobs_and_links(stage, monkeypatch, capsys):
    outcome = PipelineOutcome(
        "not_verified", 3, PIPE_URL, ("meow-review", "unit"), "fix rounds exhausted"
    )
    fake_verify(monkeypatch, stage, outcome)
    state = stage.make()
    go(state)
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("[delivery] Not verified by pipeline: fix rounds exhausted")
    assert "meow-review" in last and "unit" in last
    assert f"MR: {MR_URL}" in last and f"See {PIPE_URL};" in last
    assert last.endswith(
        "fix the failing jobs on the MR branch, or re-run the pipeline."
    )
    assert "meow resume" not in last
    pipeline = state.store.load(state.run_id).delivery["pipeline"]
    assert pipeline == {
        "status": "not_verified",
        "rounds": 3,
        "url": PIPE_URL,
        "failing": ["meow-review", "unit"],
        "detail": "fix rounds exhausted",
    }


def test_missing_token_is_not_verified_not_crash(stage, monkeypatch, capsys):
    def connect(remote, config, env):
        raise PipelineUnavailable("no GitLab token (set MEOW_GITLAB_TOKEN)")

    monkeypatch.setattr(final_delivery, "connect", connect)
    state = stage.make()
    outcome = go(state)
    assert outcome.status == "not_verified"
    assert "no GitLab token" in outcome.detail
    out = capsys.readouterr().out
    assert "MR opened" in out and "Not verified by pipeline: no GitLab token" in out
    assert state.store.load(state.run_id).phase == "delivered"


def test_mr_failure_final_line_carries_manual_link(stage, monkeypatch, capsys):
    stage.agent.error = RuntimeError("boom")
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    state = stage.make()
    go(state)
    link = state.store.load(state.run_id).delivery["pr_manual_link"]
    assert link
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("[delivery] Not verified by pipeline: ")
    assert f"Open it manually: {link}" in last
    assert "Verified by pipeline:" not in last.replace("Not verified", "")


def test_mr_failure_skips_verification(stage, monkeypatch, capsys):
    stage.agent.error = RuntimeError("boom")
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    state = stage.make()
    outcome = go(state)
    assert outcome.status == "not_verified"
    assert outcome.detail == "merge request not opened: boom"
    assert not stage.verify and not stage.connect
    out = capsys.readouterr().out
    assert "MR opened" not in out
    assert out.splitlines()[-1].startswith(
        "[delivery] Not verified by pipeline: merge request not opened: boom"
    )
    assert "meow resume" not in out


def test_already_verified_is_not_rerun(stage, monkeypatch, capsys):
    fake_verify(monkeypatch, stage, PipelineOutcome("not_verified", 0, "", (), "x"))
    stored = {
        "status": "verified",
        "rounds": 1,
        "url": PIPE_URL,
        "failing": [],
        "detail": "",
    }
    state = stage.make({"pipeline": stored, "pr": {"url": MR_URL}})
    outcome = go(state)
    assert outcome == PipelineOutcome("verified", 1, PIPE_URL, (), "")
    assert not stage.verify and not stage.connect and not stage.agent.calls
    assert capsys.readouterr().out == f"[delivery] Verified by pipeline: {PIPE_URL}\n"


def test_resume_reruns_unverified_stored_outcome(stage, monkeypatch):
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 2, PIPE_URL, (), ""))
    stored = {"status": "not_verified", "rounds": 2, "url": "", "failing": []}
    state = stage.make({"pipeline": stored})
    assert go(state).status == "verified"
    assert len(stage.verify) == 1


def test_unexpected_exception_does_not_raise(stage, monkeypatch, capsys):
    async def verify(*args, **kwargs):
        await asyncio.sleep(0)
        raise KeyError("GITLAB_TOKEN=abc123\nsecond line")

    monkeypatch.setattr(final_delivery, "verify_pipeline", verify)
    state = stage.make()
    outcome = go(state)
    assert outcome.status == "not_verified"
    assert "abc123" not in outcome.detail and "\n" not in outcome.detail
    out = capsys.readouterr().out
    assert "abc123" not in out and "Not verified by pipeline" in out
    assert state.store.load(state.run_id).phase == "delivered"


def test_cancellation_propagates(stage, monkeypatch):
    async def verify(*args, **kwargs):
        await asyncio.sleep(0)
        raise RunCancelled("stop")

    monkeypatch.setattr(final_delivery, "verify_pipeline", verify)
    with pytest.raises(RunCancelled):
        go(stage.make())


def test_run_phase_restored_to_delivered(stage, monkeypatch):
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    state = stage.make()
    go(state)
    record = state.store.load(state.run_id)
    assert record.phase == "delivered"
    assert "pipeline_waiting" in [item["phase"] for item in record.transitions]
    assert record.delivery["pr"]["url"] == MR_URL
    assert record.delivery["commit"] == "abcdef1234"


def test_settings_merge_cli_options_and_fixers_use_worktree(
    stage, monkeypatch, tmp_path
):
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    built = []
    monkeypatch.setattr(final_delivery, "LintFixAgent", lambda ctx: built.append(ctx))
    monkeypatch.setattr(final_delivery, "ReviewFixAgent", lambda ctx: built.append(ctx))
    state = stage.make({"pipeline_options": {"fix_non_meow_jobs": True}})
    go(state)
    _provider, fixers, settings = stage.verify[0]
    assert settings == {**PIPELINE, "fix_non_meow_jobs": True}
    fixers.lint()
    fixers.review()
    assert [ctx.active_working_dir() for ctx in built] == [tmp_path, tmp_path]


def test_config_without_pipeline_table_uses_defaults(stage, monkeypatch):
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    state = stage.make()
    del state.sprint.config["pipeline"]
    assert go(state).status == "verified"
    assert stage.verify[0][2] == PIPELINE


def test_failure_before_loop_keeps_round_counter(stage, monkeypatch):
    def connect(remote, config, env):
        raise PipelineUnavailable("no token")

    monkeypatch.setattr(final_delivery, "connect", connect)
    rounds = 2
    stored = {"status": "not_verified", "rounds": rounds, "url": "", "failing": []}
    state = stage.make({"pipeline": stored})
    assert go(state).rounds == rounds
    assert state.store.load(state.run_id).delivery["pipeline"]["rounds"] == rounds


def test_run_is_cancellable_while_opening_the_mr(stage, monkeypatch):
    phases = []
    original = final_delivery.open_run_merge_request

    async def opening(store, run_id, config, agents):
        phases.append(store.load(run_id).phase)
        return await original(store, run_id, config, agents)

    monkeypatch.setattr(final_delivery, "open_run_merge_request", opening)
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    state = stage.make()
    go(state)
    assert phases == ["pipeline_waiting"]
    assert state.store.load(state.run_id).phase == "delivered"


def test_long_unexpected_error_is_capped_and_redacted(stage, monkeypatch, capsys):
    monkeypatch.setenv("SOME_ENV_VALUE", "envsecret42")

    async def verify(*args, **kwargs):
        await asyncio.sleep(0)
        raise RuntimeError("x" * 5000 + " envsecret42")

    monkeypatch.setattr(final_delivery, "verify_pipeline", verify)
    state = stage.make()
    outcome = go(state)
    limit = 300
    assert len(outcome.detail) <= limit + len("...")
    assert "envsecret42" not in outcome.detail
    assert "envsecret42" not in capsys.readouterr().out
    saved = state.store.load(state.run_id).delivery["pipeline"]["detail"]
    assert saved == outcome.detail


def test_broken_stdout_and_journal_failures_never_raise(stage, monkeypatch):
    fake_verify(monkeypatch, stage, PipelineOutcome("verified", 0, PIPE_URL, (), ""))
    state = stage.make()

    def broken_print(*args, **kwargs):
        raise BrokenPipeError

    monkeypatch.setattr("builtins.print", broken_print)
    transition = state.store.transition

    def failing(run_id, phase, **patch):
        if phase == "delivered":
            raise OSError("disk full")
        return transition(run_id, phase, **patch)

    monkeypatch.setattr(state.store, "transition", failing)
    assert go(state).status == "verified"

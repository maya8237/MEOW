"""The bounded pipeline verify/fix loop, driven by a scripted fake provider."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from meow.execution import pipeline_verify
from meow.execution.pipeline_verify import PipelineFixers, verify_pipeline
from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled, request_cancel
from meow.integrations.ci_review import CiReviewError
from meow.integrations.pipeline_api import PipelineJob, PipelineSnapshot

SHA = "a" * 40
SETTINGS = {
    "max_fix_rounds": 3,
    "start_timeout": 300,
    "timeout": 3600,
    "poll_seconds": 15,
    "fix_non_meow_jobs": False,
}


def job(name: str, status: str, stage: str = "test") -> PipelineJob:
    return PipelineJob(name, name, stage, status, False, f"https://ci/{name}")


def snap(*jobs: PipelineJob, status: str = "success") -> PipelineSnapshot:
    return PipelineSnapshot("1", status, "https://ci/pipeline/1", jobs)


GREEN = snap(job("meow-review", "success"), job("unit-tests", "success"))
REVIEW_FAILED = snap(
    job("meow-review", "failed"), job("unit-tests", "success"), status="success"
)


@dataclass
class FakeProvider:
    snapshots: list = field(default_factory=lambda: [GREEN])
    lints: list = field(default_factory=lambda: [[]])
    artifacts: dict = field(default_factory=dict)
    logs: dict = field(default_factory=dict)
    shas: list = field(default_factory=list)
    name: str = "fake"

    def lint(self, repo: Path):
        return self.lints.pop(0) if len(self.lints) > 1 else self.lints[0]

    def find_pipeline(self, sha: str):
        self.shas.append(sha)
        found = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        if isinstance(found, Exception):
            raise found
        return found

    def job_log(self, job: PipelineJob) -> str:
        return self.logs.get(job.name, f"log of {job.name}")

    def review_artifact(self, job: PipelineJob, name: str):
        return self.artifacts.get(name)


class FakeFixer:
    def __init__(self, calls: list):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def fix(self, problems: str) -> str:
        self.calls.append(problems)
        return "fixed"


@dataclass
class Harness:
    store: RunStore
    run_id: str
    lint_calls: list
    review_calls: list
    announced: list
    pushes: list
    now: list

    def fixers(self) -> PipelineFixers:
        return PipelineFixers(
            lint=lambda: FakeFixer(self.lint_calls),
            review=lambda: FakeFixer(self.review_calls),
        )

    async def sleep(self, seconds: float) -> None:
        self.now[0] += seconds

    def run(self, provider, **settings):
        return asyncio.run(
            verify_pipeline(
                self.store,
                self.run_id,
                provider,
                self.fixers(),
                {**SETTINGS, **settings},
                self.announced.append,
                sleep=self.sleep,
                clock=lambda: self.now[0],
            )
        )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="meow/feature",
    )
    store.transition(
        record.id, "delivered", delivery={"commit": SHA, "pushed": True, "base": "main"}
    )
    pushes: list = []

    def fake_push(store, run_id, message):
        pushes.append(message)
        return f"{len(pushes):x}" * 40

    monkeypatch.setattr(pipeline_verify, "push_fix", fake_push)
    return Harness(store, record.id, [], [], [], pushes, [0.0])


def test_verified_on_first_pass(harness):
    outcome = harness.run(FakeProvider())

    assert outcome.status == "verified"
    assert outcome.rounds == 0
    assert outcome.url == "https://ci/pipeline/1"
    assert outcome.failing == ()
    assert harness.announced == ["Pipeline running on aaaaaaa (fix round 1 of 3)."]
    record = harness.store.load(harness.run_id)
    assert record.delivery["pipeline"]["status"] == "verified"
    assert record.delivery["base"] == "main"


def test_review_failure_is_fixed_then_verified(harness):
    provider = FakeProvider(
        snapshots=[REVIEW_FAILED, GREEN],
        artifacts={
            "verdict.json": json.dumps({"verdict": "FAIL"}),
            "review.md": "SUMMARY: bad\nSTATUS: FAIL\nfix foo",
        },
    )

    outcome = harness.run(provider)

    assert outcome.status == "verified"
    assert outcome.rounds == 1
    assert len(harness.review_calls) == 1
    assert "fix foo" in harness.review_calls[0]
    assert harness.pushes == [f"MEOW run {harness.run_id}: pipeline fix round 1"]
    assert provider.shas[0] == SHA
    assert provider.shas[-1] == "1" * 40
    assert "meow review job failed; fixing (round 1 of 3)." in harness.announced
    assert "Pipeline running on 1111111 (fix round 2 of 3)." in harness.announced
    delivery = harness.store.load(harness.run_id).delivery
    assert delivery["commit"] == "1" * 40
    assert delivery["pipeline"]["rounds"] == 1


def test_review_feedback_falls_back_to_job_log(harness):
    provider = FakeProvider(
        snapshots=[REVIEW_FAILED, GREEN], logs={"meow-review": "the job log"}
    )

    assert harness.run(provider).status == "verified"
    assert "the job log" in harness.review_calls[0]


def test_lint_errors_fixed_before_waiting(harness):
    provider = FakeProvider(lints=[["jobs:build:script config should be a list"], []])

    outcome = harness.run(provider)

    assert outcome.status == "verified"
    assert outcome.rounds == 1
    assert len(harness.lint_calls) == 1
    assert "jobs:build:script config should be a list" in harness.lint_calls[0]
    assert harness.review_calls == []
    assert harness.announced[0] == "Pipeline file invalid; fixing (round 1 of 3)."
    assert provider.shas == ["1" * 40]


def test_lint_none_is_unvalidated(harness):
    outcome = harness.run(FakeProvider(lints=[None]))

    assert outcome.status == "unvalidated"


def test_unverified_review_makes_no_edits(harness):
    provider = FakeProvider(
        snapshots=[REVIEW_FAILED],
        artifacts={
            "verdict.json": json.dumps({
                "verdict": "UNVERIFIED",
                "failure_reason": "auth failed for model API",
            })
        },
    )

    outcome = harness.run(provider)

    assert outcome.status == "unverified"
    assert "auth failed for model API" in outcome.detail
    assert harness.review_calls == harness.lint_calls == harness.pushes == []


def test_no_pipeline_after_start_timeout(harness):
    provider = FakeProvider(snapshots=[None])

    outcome = harness.run(provider)

    assert outcome.status == "no_pipeline"
    start = SETTINGS["start_timeout"]
    assert start <= harness.now[0] < start + SETTINGS["poll_seconds"]


def test_timeout_while_running(harness):
    running = snap(job("meow-review", "running"), status="running")

    outcome = harness.run(FakeProvider(snapshots=[running]))

    assert outcome.status == "not_verified"
    assert outcome.detail == "timed out"
    assert harness.now[0] >= SETTINGS["timeout"]


def test_zero_jobs_running_keeps_polling_until_timeout(harness):
    provider = FakeProvider(snapshots=[snap(status="running")])

    outcome = harness.run(provider)

    assert outcome.status == "not_verified"
    assert outcome.detail == "timed out"
    assert len(provider.shas) > SETTINGS["timeout"] // SETTINGS["poll_seconds"]


def test_zero_jobs_terminal_is_not_verified(harness):
    outcome = harness.run(FakeProvider(snapshots=[snap(status="success")]))

    assert outcome.status == "not_verified"
    assert outcome.detail == "pipeline has no jobs"


def test_review_job_skipped_is_not_verified(harness):
    provider = FakeProvider(snapshots=[snap(job("meow-review", "skipped"))])

    outcome = harness.run(provider)

    assert outcome.status == "not_verified"
    assert outcome.detail == "meow review job skipped"
    assert harness.review_calls == []


def test_no_review_job_and_green_is_verified(harness):
    outcome = harness.run(FakeProvider(snapshots=[snap(job("build", "success"))]))

    assert outcome.status == "verified"


def test_no_diff_fix_stops(harness, monkeypatch):
    monkeypatch.setattr(pipeline_verify, "push_fix", lambda *args: None)
    provider = FakeProvider(snapshots=[REVIEW_FAILED, GREEN])

    outcome = harness.run(provider)

    assert outcome.status == "not_verified"
    assert outcome.detail == "fix produced no changes"
    assert outcome.failing == ("meow-review",)
    assert provider.shas == [SHA]


def test_rounds_exhausted(harness):
    provider = FakeProvider(snapshots=[REVIEW_FAILED])

    outcome = harness.run(provider)

    assert outcome.status == "not_verified"
    assert outcome.detail == "fix rounds exhausted"
    assert outcome.rounds == SETTINGS["max_fix_rounds"]
    assert len(harness.review_calls) == SETTINGS["max_fix_rounds"]
    assert outcome.failing == ("meow-review",)


def test_zero_max_rounds_only_verifies(harness):
    outcome = harness.run(FakeProvider(snapshots=[REVIEW_FAILED]), max_fix_rounds=0)

    assert outcome.status == "not_verified"
    assert outcome.detail == "fix rounds exhausted"
    assert harness.review_calls == []


def test_other_failed_jobs_reported_not_fixed_by_default(harness):
    provider = FakeProvider(
        snapshots=[snap(job("meow-review", "success"), job("unit-tests", "failed"))]
    )

    outcome = harness.run(provider)

    assert outcome.status == "not_verified"
    assert outcome.failing == ("unit-tests",)
    assert "unit-tests" in outcome.detail
    assert harness.review_calls == []


def test_fixable_other_jobs_fixed_when_enabled_never_deploy(harness):
    failing = snap(
        job("meow-review", "success"),
        job("unit-tests", "failed"),
        job("ship", "failed", stage="deploy"),
    )
    after = snap(
        job("meow-review", "success"),
        job("unit-tests", "success"),
        job("ship", "failed", stage="deploy"),
    )
    provider = FakeProvider(
        snapshots=[failing, after], logs={"unit-tests": "assert 1 == 2"}
    )

    outcome = harness.run(provider, fix_non_meow_jobs=True)

    assert len(harness.review_calls) == 1
    assert "assert 1 == 2" in harness.review_calls[0]
    assert "log of ship" not in harness.review_calls[0]
    assert outcome.status == "not_verified"
    assert outcome.failing == ("ship",)


def test_resume_keeps_round_counter(harness):
    store, run_id = harness.store, harness.run_id
    store.transition(
        run_id,
        "pipeline_waiting",
        delivery={**store.load(run_id).delivery, "pipeline": {"rounds": 2}},
    )

    outcome = harness.run(FakeProvider(snapshots=[REVIEW_FAILED]))

    assert len(harness.review_calls) == 1
    assert outcome.rounds == SETTINGS["max_fix_rounds"]
    assert outcome.detail == "fix rounds exhausted"
    assert harness.announced[0] == "Pipeline running on aaaaaaa (fix round 3 of 3)."


@pytest.mark.parametrize(
    "error",
    [CiReviewError("pipeline API failed (HTTP 500)"), OSError("down"), ValueError()],
)
def test_provider_error_is_not_verified(harness, error):
    outcome = harness.run(FakeProvider(snapshots=[error]))

    assert outcome.status == "not_verified"
    assert outcome.detail
    assert "\n" not in outcome.detail
    record = harness.store.load(harness.run_id)
    assert record.delivery["pipeline"]["status"] == "not_verified"


def test_cancellation_propagates(harness):
    running = snap(job("meow-review", "running"), status="running")
    provider = FakeProvider(snapshots=[running])
    calls = []

    async def cancel_on_sleep(seconds):
        calls.append(seconds)
        await asyncio.sleep(0)
        request_cancel(harness.store, harness.run_id)

    harness.sleep = cancel_on_sleep

    with pytest.raises(RunCancelled):
        harness.run(provider)
    assert harness.store.load(harness.run_id).phase == "cancelled"

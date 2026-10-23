"""The `deliver` workflow step forwards the journalled delivery options."""

import asyncio
from types import SimpleNamespace

import pytest

from meow.execution import final_delivery
from meow.execution.pipeline_verify import PipelineOutcome
from meow.execution.run_state import RunStore
from meow.execution.workflow import builtin_steps


def run_deliver_step(tmp_path, monkeypatch, delivery):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(record.id, "checks_finished", delivery=delivery)
    calls = []
    monkeypatch.setattr(
        builtin_steps,
        "deliver_verified_run",
        lambda store_arg, run_id, **kwargs: calls.append((run_id, kwargs)),
    )
    monkeypatch.setattr(builtin_steps, "evidence_ready", lambda _state: True)
    state = SimpleNamespace(
        block_delivery=lambda: None,
        checks_results=[object()],
        store=store,
        run_id=record.id,
        journal=lambda *_args, **_kwargs: None,
    )
    asyncio.run(builtin_steps.deliver(state))
    return record.id, calls


def test_deliver_step_honours_journal_push_flag(tmp_path, monkeypatch):
    run_id, calls = run_deliver_step(
        tmp_path, monkeypatch, {"enabled": True, "push": False}
    )

    assert calls == [(run_id, {"commit": True, "push": False})]


@pytest.mark.parametrize("delivery", [{}, {"enabled": False}, {"enabled": True}])
def test_deliver_step_defaults_to_pushing(tmp_path, monkeypatch, delivery):
    run_id, calls = run_deliver_step(tmp_path, monkeypatch, delivery)

    assert calls == [(run_id, {"commit": delivery.get("enabled"), "push": True})]


def run_deliver_with_stage(tmp_path, monkeypatch, delivered):
    order = []
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="f", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.transition(record.id, "checks_finished", delivery={"base": "main"})

    def deliver_run(store_arg, run_id, **kwargs):
        order.append("deliver")
        return delivered

    async def final(state):
        await asyncio.sleep(0)
        order.append(("final", state.run_id))

    monkeypatch.setattr(builtin_steps, "deliver_verified_run", deliver_run)
    monkeypatch.setattr(builtin_steps, "run_final_delivery", final)
    monkeypatch.setattr(builtin_steps, "evidence_ready", lambda _state: True)
    state = SimpleNamespace(
        block_delivery=lambda: None,
        checks_results=[object()],
        store=store,
        run_id=record.id,
        journal=lambda *_args, **_kwargs: None,
    )
    asyncio.run(builtin_steps.deliver(state))
    return record.id, order


def test_deliver_step_runs_final_delivery_after_push(tmp_path, monkeypatch):
    run_id, order = run_deliver_with_stage(tmp_path, monkeypatch, True)

    assert order == ["deliver", ("final", run_id)]


def test_deliver_step_runs_final_delivery_not_when_push_returned_false(
    tmp_path, monkeypatch
):
    _run_id, order = run_deliver_with_stage(tmp_path, monkeypatch, False)

    assert order == ["deliver"]


def test_resumed_after_push_skips_evidence_and_push(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="f", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    delivery = {"base": "main", "pushed": True, "commit": "abc"}
    store.transition(record.id, "pipeline_waiting", delivery=delivery)
    finals = []

    async def final(state):
        await asyncio.sleep(0)
        finals.append(state.run_id)

    def no_push(*args, **kwargs):
        raise AssertionError("must not deliver again")

    monkeypatch.setattr(builtin_steps, "deliver_verified_run", no_push)
    monkeypatch.setattr(builtin_steps, "run_final_delivery", final)
    monkeypatch.setattr(builtin_steps, "evidence_ready", lambda _state: False)
    state = SimpleNamespace(
        block_delivery=lambda: None,
        checks_results=[object()],
        store=store,
        run_id=record.id,
        journal=lambda *_args, **_kwargs: None,
    )
    asyncio.run(builtin_steps.deliver(state))
    assert finals == [record.id]


def test_real_deliver_resumes_cancelled_stage(tmp_path, monkeypatch):
    """A stage cancelled mid-loop resumes through the real final delivery."""
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="f", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    delivery = {
        "base": "main",
        "pushed": True,
        "commit": "abc",
        "pr": {"url": "https://gitlab.com/a/b/-/merge_requests/1"},
        "pipeline": {"status": "waiting", "rounds": 1},
    }
    store.transition(record.id, "pipeline_waiting", delivery=delivery)
    store.transition(record.id, "cancelled", last_failure="Cancellation requested")

    async def verify(*args):
        await asyncio.sleep(0)
        return PipelineOutcome(
            "verified", 1, "https://gitlab.com/a/b/-/pipelines/2", (), ""
        )

    monkeypatch.setattr(
        final_delivery, "origin_url", lambda repo: "git@gitlab.com:a/b.git"
    )
    monkeypatch.setattr(final_delivery, "connect", lambda *args: object())
    monkeypatch.setattr(final_delivery, "verify_pipeline", verify)
    monkeypatch.setattr(builtin_steps, "evidence_ready", lambda _state: False)
    state = SimpleNamespace(
        block_delivery=lambda: None,
        checks_results=None,
        store=store,
        run_id=record.id,
        journal=lambda *_args, **_kwargs: None,
        sprint=SimpleNamespace(config={}),
    )
    asyncio.run(builtin_steps.deliver(state))
    saved = store.load(record.id)
    assert saved.phase == "delivered"
    assert saved.delivery["pipeline"]["status"] == "verified"
    assert saved.delivery["pipeline"]["rounds"] == 1

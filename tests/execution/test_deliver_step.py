"""The `deliver` workflow step forwards the journalled delivery options."""

import asyncio
from types import SimpleNamespace

import pytest

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

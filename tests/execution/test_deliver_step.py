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
        tmp_path, monkeypatch, {"unattended": True, "push": False}
    )

    assert calls == [(run_id, {"unattended": True, "push": False})]


@pytest.mark.parametrize("delivery", [{}, {"unattended": False}])
def test_deliver_step_defaults_to_pushing(tmp_path, monkeypatch, delivery):
    run_id, calls = run_deliver_step(tmp_path, monkeypatch, delivery)

    assert calls == [(run_id, {"unattended": False, "push": True})]

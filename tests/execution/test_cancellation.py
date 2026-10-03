"""Cancellation preserves a checkpoint and stops awaited work."""

import asyncio
import sys

import pytest

from meow.cancellation import RunCancelled, cancellable, request_cancel
from meow.cli.core import _build_arg_parser, cli_main
from meow.run_state import RunStore


def test_cancel_request_is_idempotent_and_preserves_worktree(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="change",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    request_cancel(store, record.id)
    request_cancel(store, record.id)
    assert store.load(record.id).phase == "created"
    assert tmp_path.is_dir()


def test_cancel_after_delivery_is_not_accepted(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="change",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(record.id, "delivered")
    with pytest.raises(ValueError):
        request_cancel(store, record.id)
    assert not (store.directory / f"{record.id}.cancel").exists()


def test_cancel_cli_requests_marker_without_booting_repo(tmp_path, monkeypatch, capsys):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="change",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    monkeypatch.setattr(sys, "argv", ["meow", "cancel", record.id, "-d", str(tmp_path)])
    cli_main()
    assert (store.directory / f"{record.id}.cancel").is_file()
    assert record.id in capsys.readouterr().out
    parsed = _build_arg_parser().parse_args(["worktree", "inspect", record.id])
    assert parsed.worktree_command == "inspect"


def test_cancel_stops_active_await_and_records_cancelled(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="change",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    stopped = asyncio.Event()

    async def long_operation():
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    async def exercise():
        task = asyncio.create_task(cancellable(store, record.id, long_operation()))
        await asyncio.sleep(0.02)
        request_cancel(store, record.id)
        with pytest.raises(RunCancelled):
            await task

    asyncio.run(exercise())
    assert stopped.is_set()
    assert store.load(record.id).phase == "cancelled"
    assert tmp_path.is_dir()

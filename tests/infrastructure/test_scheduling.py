"""Persistent scheduling without launching agents or modifying OS tasks."""

import json
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from meow.infrastructure import scheduling

FAILED_EXIT = 7


def future():
    return (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()


@pytest.fixture
def backend(monkeypatch, tmp_path):
    runner = Mock(return_value=Mock(stdout="DOMAIN\\user\n", stderr="", returncode=0))
    monkeypatch.setattr(scheduling.subprocess, "run", runner)
    monkeypatch.setattr(scheduling, "_unit_dir", lambda: tmp_path / "units")
    return runner


def test_time_accepts_offset_and_next_local_clock():
    now = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    assert scheduling.parse_at("19:30", now=now) == datetime(
        2026, 10, 14, 19, 30, tzinfo=UTC
    )
    assert scheduling.parse_at("2026-10-14T10:00:00+03:00", now=now) == datetime(
        2026, 10, 14, 7, tzinfo=UTC
    )
    for value in ("yesterday", "2026-10-12T10:00Z", "99:30", None):
        with pytest.raises(ValueError):
            scheduling.parse_at(value, now=now)


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_register_list_cancel_preserves_request(  # ruff: ignore[too-many-statements]
    tmp_path, monkeypatch, backend, platform
):
    monkeypatch.setattr(scheduling.sys, "platform", platform)
    request = 'Fix "search"; $(touch surprise) % & café\nsecond line'
    job = scheduling.schedule_run(
        tmp_path, future(), ["run", request, "--name", "search"]
    )
    assert job["state"] == "scheduled"
    assert request in job["argv"]
    assert "--unattended" in job["argv"]
    assert str(tmp_path.resolve()) in job["argv"]
    assert scheduling.list_schedules(tmp_path)[0]["id"] == job["id"]
    if platform == "win32":
        xml = ET.parse(tmp_path / ".meow" / "schedules" / f"{job['id']}.xml")
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        assert xml.find(".//t:Command", ns).text == sys.executable
        assert request not in xml.find(".//t:Arguments", ns).text
        assert xml.find(".//t:LogonType", ns).text == "InteractiveToken"
    else:
        timer = (tmp_path / "units" / f"meow-{job['id']}.timer").read_text()
        assert "Persistent=true" in timer
        assert "UTC" in timer
        service = (tmp_path / "units" / f"meow-{job['id']}.service").read_text()
        assert "KillMode=process" in service
        assert backend.call_args_list[-1].args[0][:3] == [
            "systemctl",
            "--user",
            "enable",
        ]
    assert scheduling.cancel_schedule(tmp_path, job["id"])["state"] == "cancelled"
    assert scheduling.execute_schedule(tmp_path, job["id"]) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["status"],
        ["run", "task"],
        ["run", "task", "--name", "x", "--no-worktree"],
        ["run", "task", "--name", "x", "--manually-approve-plan"],
        ["run", "--lint-fix"],
        ["run", "task", "--name", "x", "--work-dir", "/another"],
    ],
)
def test_invalid_run_never_registers(tmp_path, backend, argv):
    with pytest.raises(ValueError):
        scheduling.schedule_run(tmp_path, future(), argv)
    backend.assert_not_called()


def test_registration_failure_is_saved_and_reported(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(scheduling.sys, "platform", "linux")
    backend.side_effect = scheduling.subprocess.CalledProcessError(
        1, ["systemctl"], stderr="No user bus"
    )
    with pytest.raises(ValueError, match="No user bus"):
        scheduling.schedule_run(tmp_path, future(), ["run", "task", "--name", "x"])
    assert scheduling.list_schedules(tmp_path)[0]["state"] == "failed"
    assert not list((tmp_path / "units").glob("meow-*"))


def test_worker_runs_once_and_records_exit(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(scheduling.sys, "platform", "linux")
    job = scheduling.schedule_run(tmp_path, future(), ["run", "task", "--name", "x"])
    backend.reset_mock()
    backend.return_value = Mock(returncode=FAILED_EXIT)
    assert scheduling.execute_schedule(tmp_path, job["id"]) == FAILED_EXIT
    assert backend.call_args.args[0] == job["argv"]
    assert backend.call_args.kwargs["cwd"] == tmp_path.resolve()
    saved = scheduling.list_schedules(tmp_path)[0]
    assert saved["state"] == "failed"
    assert saved["exit_code"] == FAILED_EXIT
    backend.reset_mock()
    assert scheduling.execute_schedule(tmp_path, job["id"]) == 0
    backend.assert_not_called()


def test_imminent_worker_waits_through_registration(tmp_path, monkeypatch, backend):
    import time
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(scheduling.sys, "platform", "linux")
    job = scheduling.schedule_run(tmp_path, future(), ["run", "task", "--name", "x"])
    lock = scheduling._path(tmp_path, job["id"]).with_suffix(".lock")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with scheduling._file_transaction(lock, "registration"):
            worker = pool.submit(scheduling.execute_schedule, tmp_path, job["id"])
            # OS registration can legitimately outlast the default metadata wait.
            time.sleep(5.2)
            assert not worker.done()
        assert worker.result(timeout=5) == 0
    assert scheduling.list_schedules(tmp_path)[0]["state"] == "succeeded"


def test_invalid_id_cannot_escape_store(tmp_path):
    with pytest.raises(ValueError, match="identifier"):
        scheduling.cancel_schedule(tmp_path, "../../elsewhere")


def test_ui_execution_uses_frozen_recipe_after_edit(tmp_path, monkeypatch, backend):
    from dataclasses import replace

    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import Recipe, SessionStore

    monkeypatch.setattr(scheduling.sys, "platform", "linux")
    monkeypatch.setattr("meow.ui.sessions.process_identity", lambda _pid: "test-owner")
    frozen = Recipe(
        id="a" * 32,
        name="feature",
        repo=str(tmp_path.resolve()),
        mode="cli",
        command="run",
        request="original request",
        workflow=None,
        inputs={},
        arguments=[],
        options={"unattended": True},
    )
    store = SessionStore(tmp_path)
    store.save_recipe(frozen)
    job = scheduling.schedule_session(tmp_path, future(), frozen)
    store.update_recipe(frozen.id, replace(frozen, request="edited request"))
    launcher = Mock(return_value=Mock(id="b" * 32))
    monkeypatch.setattr(ui_sessions, "launch_attempt", launcher)
    assert scheduling.execute_schedule(tmp_path, job["id"]) == 0
    assert launcher.call_args.kwargs["recipe"] == frozen
    assert scheduling.list_schedules(tmp_path)[0]["state"] == "launched"


def test_cli_schedule_does_not_run_now(tmp_path, monkeypatch, backend, capsys):
    from meow.cli.cli import cli_main

    monkeypatch.setattr(scheduling.sys, "platform", "linux")
    cli_main([
        "schedule",
        "add",
        "--at",
        future(),
        "-d",
        str(tmp_path),
        "--",
        "run",
        "task",
        "--name",
        "x",
    ])
    job = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert job["state"] == "scheduled"
    assert all(call.args[0][0] != sys.executable for call in backend.call_args_list)

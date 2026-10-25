from __future__ import annotations

import json
import os
import subprocess
import threading
import time

import pytest

from meow.common.process import process_identity
from meow.execution.run_state import RunStateError, RunStore
from meow.infrastructure import ui_sessions
from meow.project.config import load_config
from meow.project.custom.workflows import fingerprint
from meow.ui.sessions import SessionStore

SESSION_ID = "a" * 32
ATTEMPT_ID = "b" * 32


def make_run(store: RunStore, **changes):
    record = store.create(
        source="prompt",
        request="implement the feature",
        repo=store.directory.parent.parent,
        worktree=store.directory.parent.parent,
        branch="main",
    )
    phase = changes.pop("phase", record.phase)
    if changes or phase != record.phase:
        store.transition(record.id, phase, **changes)
    return store.load(record.id)


def test_run_store_persists_explicit_worker_ui_session_association(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEOW_UI_SESSION_ID", SESSION_ID)
    monkeypatch.setenv("MEOW_UI_ATTEMPT_ID", ATTEMPT_ID)
    monkeypatch.setenv("MEOW_UI_REPO", str(tmp_path.resolve()))
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="task", repo=tmp_path, worktree=tmp_path, branch="main"
    )

    saved = store.load(record.id)
    assert saved.ui_session_id == SESSION_ID
    assert saved.ui_attempt_id == ATTEMPT_ID


@pytest.mark.parametrize(
    "field,value",
    [("ui_session_id", "../runs"), ("ui_attempt_id", "A" * 32)],
)
def test_run_store_rejects_invalid_explicit_ui_ids(tmp_path, field, value):
    with pytest.raises(ValueError):
        RunStore(tmp_path).create(
            source="prompt",
            request="task",
            repo=tmp_path,
            worktree=tmp_path,
            branch="main",
            **{field: value},
        )


@pytest.mark.parametrize(
    "context",
    [
        {"MEOW_UI_SESSION_ID": SESSION_ID},
        {
            "MEOW_UI_SESSION_ID": SESSION_ID,
            "MEOW_UI_ATTEMPT_ID": ATTEMPT_ID,
            "MEOW_UI_REPO": "other-repo",
        },
    ],
)
def test_run_store_rejects_partial_or_mismatched_worker_context(
    tmp_path, monkeypatch, context
):
    for key in ("MEOW_UI_SESSION_ID", "MEOW_UI_ATTEMPT_ID", "MEOW_UI_REPO"):
        monkeypatch.delenv(key, raising=False)
    for key, value in context.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(RunStateError):
        RunStore(tmp_path).create(
            source="prompt",
            request="task",
            repo=tmp_path,
            worktree=tmp_path,
            branch="main",
        )


def test_unassociated_legacy_run_loads_without_ui_fields(tmp_path):
    store = RunStore(tmp_path)
    record = make_run(store)
    data = json.loads(store._path(record.id).read_text(encoding="utf-8"))
    data.pop("ui_session_id", None)
    data.pop("ui_attempt_id", None)
    store._path(record.id).write_text(json.dumps(data), encoding="utf-8")

    saved = store.load(record.id)
    assert saved.ui_session_id is None
    assert saved.ui_attempt_id is None


def test_imported_runs_include_phase_output_availability_and_session_link(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / ".meow" / "workflows").mkdir(parents=True)
    (tmp_path / ".meow" / "workflows" / "audit.toml").write_text(
        'description="Audit"\n[[steps]]\nid="first"\nagent="explorer"\n',
        encoding="utf-8",
    )
    (tmp_path / ".meow" / "config.toml").write_text(
        '[workflow]\ndefault="audit"\n', encoding="utf-8"
    )
    catalog = load_config(tmp_path)["customizations"]
    store = RunStore(tmp_path)
    record = make_run(
        store,
        phase="planning",
        results={
            "feature_name": "feature",
            "workflow": {
                "name": "audit",
                "scope": "project",
                "fingerprint": fingerprint(catalog.workflow("audit"), catalog),
                "completed": [],
                "active": None,
                "inputs": {"x": "y"},
            },
        },
    )
    record = store.transition(
        record.id, record.phase, ui_session_id=SESSION_ID, ui_attempt_id=ATTEMPT_ID
    )
    log = store.directory / f"{record.id}.log"
    log.write_text("saved output\n", encoding="utf-8")

    imported = ui_sessions.list_imported_runs(tmp_path)[0]
    assert imported == {
        "id": record.id,
        "repo": str(tmp_path.resolve()),
        "source": "prompt",
        "request": "implement the feature",
        "phase": "planning",
        "created_at": record.created_at,
        "updated_at": store.load(record.id).updated_at,
        "worktree": str(tmp_path.resolve()),
        "branch": "main",
        "feature_name": "feature",
        "workflow": {"name": "audit", "scope": "project", "inputs": {"x": "y"}},
        "plan_file": None,
        "background_output_available": True,
        "ui_session_id": SESSION_ID,
        "ui_attempt_id": ATTEMPT_ID,
        "resume_supported": True,
        "cancel_supported": False,
    }


def test_imported_run_without_history_reports_no_output_and_no_cancel(tmp_path):
    store = RunStore(tmp_path)
    record = make_run(store, phase="complete")

    imported = ui_sessions.serialize_imported_run(tmp_path, record.id)
    assert imported["background_output_available"] is False
    assert imported["ui_session_id"] is None
    assert imported["ui_attempt_id"] is None
    assert imported["resume_supported"] is False
    assert imported["cancel_supported"] is False


def test_imported_run_actions_follow_current_worker_inspection(tmp_path):
    store = RunStore(tmp_path)
    record = make_run(store, phase="planning")
    store.update_background(record.id, launch_started_at=time.time())

    imported = ui_sessions.serialize_imported_run(tmp_path, record.id)

    assert imported["resume_supported"] is False
    assert imported["cancel_supported"] is True


def test_run_output_uses_fixed_log_and_bounded_cursor(tmp_path):
    store = RunStore(tmp_path)
    record = make_run(store)
    path = store.directory / f"{record.id}.log"
    path.write_text("a" * 20, encoding="utf-8")
    store.update_background(record.id, log=str(tmp_path / "outside.log"))

    page = ui_sessions.read_run_output(tmp_path, record.id, cursor=0, limit=8)
    assert page == {
        "text": "a" * 8,
        "next_cursor": 8,
        "size": 20,
        "truncated": True,
        "reset": False,
    }
    assert ui_sessions.run_output_path(tmp_path, record.id) == path


def test_imported_run_resume_creates_detached_attempt_linked_to_existing_run(
    tmp_path, monkeypatch
):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    store = RunStore(tmp_path)
    record = make_run(store, phase="planned")
    launched = []

    def launch(session_store, attempt):
        launched.append((session_store, attempt))
        return attempt

    monkeypatch.setattr(ui_sessions, "_launch_owned_attempt", launch)
    session, attempt = ui_sessions.resume_imported_run(tmp_path, record.id, repair=True)

    saved_attempt = SessionStore(tmp_path)._read_attempt(session.id, attempt.id)
    assert len(launched) == 1
    assert saved_attempt.run_ids == [record.id]
    assert saved_attempt.argv[-1] == "--repair"
    assert saved_attempt.argv[:5] == [
        ui_sessions.sys.executable,
        "-m",
        "meow",
        "resume",
        record.id,
    ]
    assert session.id == saved_attempt.session_id


def test_imported_run_cannot_be_resumed_twice_after_worker_registration(  # ruff: ignore[too-many-statements]
    tmp_path, monkeypatch
):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    record = make_run(RunStore(tmp_path), phase="planned")
    launched = []
    registered = threading.Event()
    release = threading.Event()

    def launch(session_store, attempt):
        identity = process_identity(os.getpid())
        running = session_store.update_attempt(
            attempt.session_id,
            attempt.id,
            state="running",
            worker={"pid": os.getpid(), "identity": identity, "token": "test"},
        )
        assert not ui_sessions.attempt_resume_status(session_store.repo, running)[
            "resume_supported"
        ]
        launched.append(running)
        registered.set()
        assert release.wait(5)
        return running

    monkeypatch.setattr(ui_sessions, "_launch_owned_attempt", launch)
    first = threading.Thread(
        target=lambda: ui_sessions.resume_imported_run(tmp_path, record.id)
    )
    first.start()
    assert registered.wait(5)

    try:
        with pytest.raises(RuntimeError, match="in progress"):
            ui_sessions.resume_imported_run(tmp_path, record.id)
    finally:
        release.set()
    first.join(5)
    assert not first.is_alive()
    with pytest.raises(RuntimeError, match="active UI resume"):
        ui_sessions.resume_imported_run(tmp_path, record.id)
    assert len(launched) == 1


def test_owned_resume_validates_linked_run_before_creating_attempt(tmp_path):
    store = RunStore(tmp_path)
    record = make_run(store, phase="complete")
    sessions = SessionStore(tmp_path)
    recipe = ui_sessions.Recipe(
        id=SESSION_ID,
        name="Resume",
        repo=str(tmp_path.resolve()),
        mode="cli",
        command="run",
        request="task",
        workflow=None,
        inputs={},
        arguments=[],
        options={},
    )
    session = sessions.save_recipe(recipe)
    attempt = sessions.create_attempt(session.id, recipe, "token")
    sessions.update_attempt(
        session.id, attempt.id, state="stopped", run_ids=[record.id]
    )

    with pytest.raises(ui_sessions.SessionError, match="cannot be resumed"):
        ui_sessions.resume_attempt(tmp_path, session.id, attempt.id)
    assert len(sessions.load(session.id).attempts) == 1


def test_reconcile_attempt_marks_dead_worker_interrupted_and_keeps_startup_grace(
    tmp_path, monkeypatch
):
    sessions = SessionStore(tmp_path)
    recipe = ui_sessions.Recipe(
        id=SESSION_ID,
        name="Reconcile",
        repo=str(tmp_path.resolve()),
        mode="cli",
        command="run",
        request="task",
        workflow=None,
        inputs={},
        arguments=[],
        options={},
    )
    session = sessions.save_recipe(recipe)
    attempt = sessions.create_attempt(session.id, recipe, "token")
    attempt = sessions.update_attempt(
        session.id,
        attempt.id,
        state="running",
        worker={"pid": 4242, "identity": "dead", "token": "test"},
    )
    monkeypatch.setattr(ui_sessions, "process_identity", lambda _pid: None)

    reconciled = ui_sessions.reconcile_attempt(tmp_path, session.id, attempt.id)
    assert reconciled.state == "interrupted"

    starting = sessions.create_attempt(session.id, recipe, "token-2")
    assert (
        ui_sessions.reconcile_attempt(tmp_path, session.id, starting.id).state
        == "starting"
    )


def test_cancel_imported_run_requests_cooperative_cancel(tmp_path):
    store = RunStore(tmp_path)
    record = make_run(store, phase="planning")
    store.update_background(record.id, launch_started_at=time.time())

    result = ui_sessions.cancel_imported_run(tmp_path, record.id)

    assert result.id == record.id
    assert store._path(record.id).with_suffix(".cancel").is_file()

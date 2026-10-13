from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from meow.ui.sessions import Recipe, new_id


def valid_recipe(repo: Path) -> Recipe:
    return Recipe(
        id=new_id(),
        name="example",
        repo=str(repo),
        mode="cli",
        command="run",
        request="print a result",
        workflow=None,
        inputs={},
        arguments=[],
        options={"unattended": True},
    )


def test_store_round_trips_recipe_and_distinct_attempt_logs(tmp_path):
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    first = store.create_attempt(recipe.id, recipe, worker_token="token-a")
    store.finish_attempt(recipe.id, first.id, state="succeeded", exit_code=0)
    second = store.create_attempt(recipe.id, recipe, worker_token="token-b")
    store.append_output(recipe.id, first.id, b"first\n")
    store.append_output(recipe.id, second.id, b"second\n")

    assert store.load(recipe.id).attempts == [first.id, second.id]
    assert store.read_output(recipe.id, first.id, 0, 20)["text"] == "first\n"
    assert store.read_output(recipe.id, second.id, 0, 20)["text"] == "second\n"


def test_claim_prevents_two_active_attempts_for_same_session(tmp_path):
    from meow.ui.sessions import SessionBusy, SessionStore

    store = SessionStore(tmp_path)
    session = store.save_recipe(valid_recipe(tmp_path))
    other_store = SessionStore(tmp_path)
    with (
        store.claim(session.id, "launch"),
        pytest.raises(SessionBusy),
        other_store.claim(session.id, "launch"),
    ):
        pass


def test_claim_recovers_dead_identity_but_keeps_live_owner(tmp_path):
    import json

    from meow.ui.sessions import SessionBusy, SessionStore

    store = SessionStore(tmp_path)
    session = store.save_recipe(valid_recipe(tmp_path))
    lock = store._directory(session.id) / "claim.json"
    lock.write_text(json.dumps({"pid": 99999999, "identity": "dead-birth"}))

    with store.claim(session.id, "launch"):
        assert json.loads(lock.read_text())["action"] == "launch"

    with (
        store.claim(session.id, "launch"),
        pytest.raises(SessionBusy),
        SessionStore(tmp_path).claim(session.id, "launch"),
    ):
        pass


def test_active_attempt_cannot_be_archived_and_keeps_recipe_snapshot(tmp_path):
    from dataclasses import replace

    from meow.ui.sessions import SessionBusy, SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.update_attempt(recipe.id, attempt.id, state="running")

    with pytest.raises(SessionBusy):
        store.archive(recipe.id, True)
    with pytest.raises(SessionBusy):
        store.create_attempt(recipe.id, recipe, worker_token="another")
    store.update_recipe(recipe.id, replace(recipe, request="edit"))
    assert store.load(recipe.id).recipe["request"] == "edit"
    assert (
        store._read_attempt(recipe.id, attempt.id).effective_recipe["request"]
        == "print a result"
    )


def test_recipe_edit_keeps_effective_recipe_snapshot(tmp_path):
    from dataclasses import replace

    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.finish_attempt(recipe.id, attempt.id, "succeeded", 0)
    updated = replace(recipe, request="changed request")

    store.update_recipe(recipe.id, updated)

    assert store.load(recipe.id).recipe["request"] == "changed request"
    assert (
        store._read_attempt(recipe.id, attempt.id).effective_recipe["request"]
        == "print a result"
    )


def test_output_rotation_and_truncation_reset_cursor(tmp_path):
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.append_output(recipe.id, attempt.id, b"original output")
    store.read_output(recipe.id, attempt.id, 0, 100)
    output = store._directory(recipe.id) / attempt.output_file
    output.write_bytes(b"tiny")

    assert store.read_output(recipe.id, attempt.id, 4, 100)["reset"] is True

    store.read_output(recipe.id, attempt.id, 0, 100)
    rotated = output.with_suffix(".old")
    output.replace(rotated)
    output.write_bytes(b"rotated")
    result = store.read_output(recipe.id, attempt.id, 0, 100)
    assert result["reset"] is True
    assert result["text"] == "rotated"


def test_registration_timeout_persists_failed_attempt(tmp_path, monkeypatch):
    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionStore

    class ExitedWorker:
        pid = 123

        @staticmethod
        def poll():
            return 1

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    monkeypatch.setattr(ui_sessions, "command_for", lambda _recipe: [sys.executable])
    monkeypatch.setattr(ui_sessions.subprocess, "Popen", lambda *a, **k: ExitedWorker())

    with pytest.raises(ValueError, match="register"):
        ui_sessions.launch_attempt(tmp_path, recipe.id)

    saved = store.load(recipe.id)
    assert saved.attempts
    failed = store._read_attempt(recipe.id, saved.attempts[0])
    assert failed.state == "failed"
    assert failed.error == "Worker registration timed out"


def test_stop_terminates_deterministic_child_after_cooperative_request(
    tmp_path, monkeypatch
):
    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    monkeypatch.setattr(
        ui_sessions,
        "command_for",
        lambda _recipe: [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)']); "
            "print(f'grandchild={child.pid}', flush=True); time.sleep(30)",
        ],
    )
    attempt = ui_sessions.launch_attempt(tmp_path, recipe.id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        output = store.read_output(recipe.id, attempt.id, 0, 200)["text"]
        if "grandchild=" in output:
            break
        time.sleep(0.05)
    grandchild_pid = int(output.split("grandchild=")[1].splitlines()[0])
    grandchild_identity = ui_sessions.process_identity(grandchild_pid)

    stopped = ui_sessions.stop_attempt(tmp_path, recipe.id, attempt.id)

    assert stopped.state == "stopped", stopped.error
    assert grandchild_identity is not None
    assert ui_sessions.process_identity(grandchild_pid) != grandchild_identity


def test_output_cursor_stays_on_utf8_boundary_and_detects_truncation(tmp_path):
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.append_output(recipe.id, attempt.id, "éclair".encode())

    page = store.read_output(recipe.id, attempt.id, 1, 20)
    assert page == {
        "text": "éclair",
        "next_cursor": 7,
        "size": 7,
        "truncated": False,
        "reset": False,
    }


def test_launch_attempt_runs_deterministic_python_child(tmp_path, monkeypatch):
    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    monkeypatch.setattr(
        ui_sessions,
        "command_for",
        lambda _recipe: [sys.executable, "-c", "print('worker-output')"],
    )

    attempt = ui_sessions.launch_attempt(tmp_path, recipe.id)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        latest = store._read_attempt(recipe.id, attempt.id)
        if latest.state in {"succeeded", "failed"}:
            break
        time.sleep(0.05)

    assert latest.state == "succeeded"
    assert (
        store.read_output(recipe.id, attempt.id, 0, 100)["text"].replace("\r\n", "\n")
        == "worker-output\n"
    )


def test_stop_refuses_to_signal_a_reused_worker_pid(tmp_path, monkeypatch):
    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.update_attempt(
        recipe.id,
        attempt.id,
        state="running",
        worker={"pid": 4321, "identity": "birth-a", "token": "token"},
    )
    signaled = []
    monkeypatch.setattr(ui_sessions, "process_identity", lambda _pid: "birth-b")
    monkeypatch.setattr(
        ui_sessions, "_signal_owned_tree", lambda *args, **kwargs: signaled.append(args)
    )

    stopped = ui_sessions.stop_attempt(tmp_path, recipe.id, attempt.id)

    assert stopped.state == "interrupted"
    assert signaled == []


def test_restart_reconciliation_marks_missing_worker_interrupted(tmp_path, monkeypatch):
    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.update_attempt(
        recipe.id,
        attempt.id,
        state="running",
        worker={"pid": 4321, "identity": "old-birth", "token": "token"},
    )
    monkeypatch.setattr(ui_sessions, "process_identity", lambda _pid: None)

    recovered = ui_sessions.reconcile_attempt(tmp_path, recipe.id, attempt.id)

    assert recovered.state == "interrupted"


def test_linked_cli_resume_uses_exact_run_id_and_requested_repair_flags(tmp_path):
    from meow.infrastructure.ui_sessions import resume_argv
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.update_attempt(recipe.id, attempt.id, run_ids=["a" * 32])

    argv = resume_argv(
        store._read_attempt(recipe.id, attempt.id), "continue the work", repair=True
    )

    assert argv[-6:] == [
        "resume",
        "a" * 32,
        "--continue",
        "--request",
        "continue the work",
        "--repair",
    ]


def test_claude_resume_keeps_server_owned_permission_flags(tmp_path, monkeypatch):
    from dataclasses import replace

    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = replace(valid_recipe(tmp_path), mode="claude", command="prompt")
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    store.update_attempt(recipe.id, attempt.id, claude_session_id="saved-session")
    monkeypatch.setattr(
        ui_sessions,
        "command_for",
        lambda _recipe: [
            "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--",
            "old prompt",
        ],
    )
    monkeypatch.setattr("meow.ui.sessions.claude_path", lambda: "claude")

    argv = ui_sessions.resume_argv(
        store._read_attempt(recipe.id, attempt.id), "new prompt"
    )

    assert argv[:3] == ["claude", "--resume", "saved-session"]
    assert argv[-1] == "new prompt\n\nWork unattended. Do not wait for human input."
    assert argv[argv.index("--permission-prompts") + 1] == "none"


def test_claude_partial_text_is_not_duplicated_and_raw_events_survive(tmp_path):
    import json

    from meow.infrastructure.ui_sessions import _render_claude_line
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    shown = []
    partial = (
        json.dumps({
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "answer"}},
        }).encode()
        + b"\n"
    )
    result = (
        json.dumps({
            "type": "result",
            "session_id": "claude-session",
            "result": "answer",
        }).encode()
        + b"\n"
    )

    _render_claude_line(store, recipe.id, attempt.id, partial, shown)
    _render_claude_line(store, recipe.id, attempt.id, result, shown)

    assert store.read_output(recipe.id, attempt.id, 0, 100)["text"] == "answer"
    raw = (store._directory(recipe.id) / f"{attempt.id}.raw").read_bytes()
    assert raw == partial + result
    assert (
        store._read_attempt(recipe.id, attempt.id).claude_session_id == "claude-session"
    )

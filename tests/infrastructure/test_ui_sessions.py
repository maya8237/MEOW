from __future__ import annotations

import sys
import threading
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
    assert store.directory == tmp_path.resolve() / ".meow" / "ui" / "sessions"


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
    session = store.load(recipe.id)
    assert session.recipe["request"] == "edit"
    assert session.state == "running"
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


def test_concurrent_attempt_updates_preserve_disjoint_fields(  # ruff: ignore[complex-structure, too-many-statements]
    tmp_path, monkeypatch
):
    from meow.ui import sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    attempt = store.create_attempt(recipe.id, recipe, worker_token="token")
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    second_write_started = threading.Event()
    errors = []
    original_atomic_json = sessions._atomic_json

    def controlled_atomic_json(path, value):
        if path.name == f"{attempt.id}.json":
            if threading.current_thread().name == "first-writer":
                first_write_started.set()
                if not release_first_write.wait(5):
                    raise TimeoutError("first write was not released")
            elif threading.current_thread().name == "second-writer":
                second_write_started.set()
        original_atomic_json(path, value)

    monkeypatch.setattr(sessions, "_atomic_json", controlled_atomic_json)

    def update(**changes):
        try:
            store.update_attempt(recipe.id, attempt.id, **changes)
        except BaseException as exc:  # surface worker-thread failures in the test
            errors.append(exc)

    first = threading.Thread(
        target=update, name="first-writer", kwargs={"error": "first"}
    )
    second = threading.Thread(
        target=update,
        name="second-writer",
        kwargs={"claude_session_id": "second"},
    )
    first.start()
    assert first_write_started.wait(5)
    second.start()
    try:
        assert not second_write_started.wait(0.1)
    finally:
        release_first_write.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    saved = store._read_attempt(recipe.id, attempt.id)
    assert saved.error == "first"
    assert saved.claude_session_id == "second"


def test_concurrent_recipe_edit_and_attempt_creation_preserve_both(  # ruff: ignore[complex-structure, too-many-statements]
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from meow.ui import sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    original = store.create_attempt(recipe.id, recipe, worker_token="first")
    store.finish_attempt(recipe.id, original.id, "succeeded", 0)
    edited = replace(recipe, request="edited while the next attempt starts")
    edit_write_started = threading.Event()
    release_edit_write = threading.Event()
    launch_write_started = threading.Event()
    errors = []
    original_atomic_json = sessions._atomic_json

    def controlled_atomic_json(path, value):
        if path.name == "session.json":
            if threading.current_thread().name == "recipe-editor":
                edit_write_started.set()
                if not release_edit_write.wait(5):
                    raise TimeoutError("recipe edit was not released")
            elif threading.current_thread().name == "attempt-launcher":
                launch_write_started.set()
        original_atomic_json(path, value)

    monkeypatch.setattr(sessions, "_atomic_json", controlled_atomic_json)

    def edit():
        try:
            store.update_recipe(recipe.id, edited)
        except BaseException as exc:
            errors.append(exc)

    def launch():
        try:
            store.create_attempt(recipe.id, edited, worker_token="second")
        except BaseException as exc:
            errors.append(exc)

    editor = threading.Thread(target=edit, name="recipe-editor")
    launcher = threading.Thread(target=launch, name="attempt-launcher")
    editor.start()
    assert edit_write_started.wait(5)
    launcher.start()
    try:
        assert not launch_write_started.wait(0.1)
    finally:
        release_edit_write.set()
    editor.join(5)
    launcher.join(5)

    assert not editor.is_alive() and not launcher.is_alive()
    assert errors == []
    saved = store.load(recipe.id)
    assert saved.recipe["request"] == edited.request
    assert saved.attempts[0] == original.id
    assert saved.attempts[-1] != original.id
    assert (
        store._read_attempt(recipe.id, saved.attempts[-1]).effective_recipe["request"]
        == edited.request
    )


def test_finishing_previous_attempt_cannot_overwrite_next_starting_state(  # ruff: ignore[complex-structure, too-many-statements]
    tmp_path, monkeypatch
):
    from contextlib import contextmanager

    from meow.ui import sessions
    from meow.ui.sessions import SessionStore

    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    previous = store.create_attempt(recipe.id, recipe, worker_token="first")
    store.update_attempt(recipe.id, previous.id, state="running")
    old_sync_waiting = threading.Event()
    release_old_sync = threading.Event()
    next_attempt_created = threading.Event()
    errors = []
    original_file_transaction = sessions._file_transaction

    @contextmanager
    def pause_old_session_sync(path, resource):
        if resource == "session" and threading.current_thread().name == "old-finisher":
            old_sync_waiting.set()
            if not release_old_sync.wait(5):
                raise TimeoutError("old attempt sync was not released")
        with original_file_transaction(path, resource):
            yield

    monkeypatch.setattr(sessions, "_file_transaction", pause_old_session_sync)

    def finish_previous():
        try:
            store.finish_attempt(recipe.id, previous.id, "succeeded", 0)
        except BaseException as exc:
            errors.append(exc)

    def create_next():
        try:
            store.create_attempt(recipe.id, recipe, worker_token="second")
            next_attempt_created.set()
        except BaseException as exc:
            errors.append(exc)

    finisher = threading.Thread(target=finish_previous, name="old-finisher")
    creator = threading.Thread(target=create_next, name="next-launcher")
    finisher.start()
    assert old_sync_waiting.wait(5)
    creator.start()
    try:
        assert next_attempt_created.wait(5)
    finally:
        release_old_sync.set()
    finisher.join(5)
    creator.join(5)

    assert not finisher.is_alive() and not creator.is_alive()
    assert errors == []
    saved = store.load(recipe.id)
    assert saved.attempts[0] == previous.id
    assert saved.attempts[-1] != previous.id
    assert saved.state == "starting"
    assert store._read_attempt(recipe.id, saved.attempts[-1]).state == "starting"


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


@pytest.mark.parametrize("failure", ["popen", "registration"])
def test_resume_launch_failure_finishes_attempt_with_error(  # ruff: ignore[complex-structure, too-many-statements]
    tmp_path, monkeypatch, failure
):
    import subprocess

    from meow.execution.run_state import RunStore
    from meow.infrastructure import ui_sessions
    from meow.ui.sessions import SessionError, SessionStore

    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    run = RunStore(tmp_path).create(
        source="prompt",
        request="implement the feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="main",
    )
    store = SessionStore(tmp_path)
    recipe = valid_recipe(tmp_path)
    store.save_recipe(recipe)
    original = store.create_attempt(recipe.id, recipe, worker_token="original")
    store.update_attempt(recipe.id, original.id, run_ids=[run.id])
    store.finish_attempt(recipe.id, original.id, "interrupted", None)
    monkeypatch.setattr(ui_sessions, "_REGISTER_SECONDS", 0)
    real_popen = subprocess.Popen

    if failure == "popen":

        def fail_popen(*args, **kwargs):
            if args and args[0][0] == "git":
                return real_popen(*args, **kwargs)
            raise OSError("worker executable missing")

        monkeypatch.setattr(ui_sessions.subprocess, "Popen", fail_popen)
        expected = "Worker launch failed: worker executable missing"
    else:

        class ExitedWorker:
            pid = 123

            @staticmethod
            def poll():
                return 1

        def exited_worker(*args, **kwargs):
            if args and args[0][0] == "git":
                return real_popen(*args, **kwargs)
            return ExitedWorker()

        monkeypatch.setattr(ui_sessions.subprocess, "Popen", exited_worker)
        expected = "Worker registration timed out"

    with pytest.raises(SessionError):
        ui_sessions.resume_attempt(tmp_path, recipe.id, original.id)

    session = store.load(recipe.id)
    resumed = store._read_attempt(recipe.id, session.attempts[-1])
    assert resumed.state == "failed"
    assert resumed.error == expected
    with store.claim(recipe.id, "launch"):
        pass


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


def test_cli_output_streams_flushed_partial_text_before_child_exit(
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
            "import sys,time; print('partial',end='',flush=True); time.sleep(6)",
        ],
    )

    attempt = ui_sessions.launch_attempt(tmp_path, recipe.id)
    deadline = time.monotonic() + 2
    try:
        while time.monotonic() < deadline:
            latest = store._read_attempt(recipe.id, attempt.id)
            output = store.read_output(recipe.id, attempt.id, 0, 100)["text"]
            if output:
                break
            time.sleep(0.05)
        assert output == "partial"
        assert latest.state == "running"
    finally:
        ui_sessions.stop_attempt(tmp_path, recipe.id, attempt.id)


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

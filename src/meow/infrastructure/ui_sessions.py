"""Detached process ownership for local UI sessions."""

from __future__ import annotations

import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

from meow.common.process import process_identity
from meow.ui.sessions import (
    Attempt,
    Recipe,
    Session,
    SessionError,
    SessionStore,
    command_for,
)

_REGISTER_SECONDS = 8
_STOP_GRACE_SECONDS = 2
_UTF8_CONTINUATION = 0x80


def _worker_env() -> dict[str, str]:
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source, existing]))
    return env


def _recipe(data: dict) -> Recipe:
    return Recipe(**data)


def reconcile_attempt(repo: Path, session_id: str, attempt_id: str) -> Attempt:
    """Mark an unowned active attempt interrupted; never relaunch it."""
    store = SessionStore(repo)
    attempt = store._read_attempt(session_id, attempt_id)
    if attempt.state in {"starting", "running", "stopping"}:
        pid = attempt.worker.get("pid")
        identity = attempt.worker.get("identity")
        if (
            not isinstance(pid, int)
            or not isinstance(identity, str)
            or process_identity(pid) != identity
        ):
            return store.finish_attempt(
                session_id, attempt_id, "interrupted", attempt.exit_code
            )
    return attempt


def _worker_argv(repo: Path, session_id: str, attempt_id: str, token: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "meow.infrastructure.ui_session_worker",
        str(repo),
        session_id,
        attempt_id,
        token,
    ]


def launch_attempt(repo: Path, session_id: str) -> Attempt:
    store = SessionStore(repo)
    with store.claim(session_id, "launch"):
        session = store.load(session_id)
        recipe = _recipe(session.recipe)
        argv = command_for(recipe)
        token = secrets.token_hex(16)
        attempt = store.create_attempt(session_id, recipe, worker_token=token)
        store.update_attempt(session_id, attempt.id, argv=argv)
        return _launch_owned_attempt(store, attempt)


def _linked_run_ids(repo: Path, session_id: str, attempt_id: str) -> list[str]:
    from meow.execution.run_state import RunStore

    store = RunStore(repo)
    runs = []
    if store.directory.is_dir():
        for path in store.directory.glob("*.json"):
            with suppress(Exception):
                record = store.load(path.stem)
                if (
                    getattr(record, "ui_session_id", None) == session_id
                    and getattr(record, "ui_attempt_id", None) == attempt_id
                ):
                    runs.append(record.id)
    return runs


def run_output_path(repo: Path, run_id: str) -> Path:
    """Return the fixed log path for a validated run, never its journal path."""
    from meow.execution.run_state import RunStore

    store = RunStore(repo)
    store.load(run_id)
    path = store.directory / f"{run_id}.log"
    try:
        store.directory.resolve().relative_to(Path(repo).resolve())
        path.resolve().relative_to(store.directory.resolve())
    except ValueError as exc:
        raise SessionError("Run output path is outside the run journal") from exc
    if path.is_symlink():
        raise SessionError("Run output path cannot be a symbolic link")
    return path


def read_run_output(repo: Path, run_id: str, cursor: int, limit: int) -> dict:
    """Read a bounded UTF-8 page from the run's known background log."""
    path = run_output_path(repo, run_id)
    if not path.is_file():
        raise SessionError("No background output was recorded for this run")
    limit = min(max(int(limit), 1), 64 * 1024)
    cursor = max(0, int(cursor))
    size = path.stat().st_size
    cursor = min(cursor, size)
    with path.open("rb") as stream:
        while cursor and cursor < size:
            stream.seek(cursor)
            if stream.read(1)[0] & 0xC0 != _UTF8_CONTINUATION:
                break
            cursor -= 1
        stream.seek(cursor)
        data = stream.read(min(limit + 4, size - cursor))[:limit]
    text, data = _decode_output_page(data)
    next_cursor = cursor + len(data)
    return {
        "text": text,
        "next_cursor": next_cursor,
        "size": size,
        "truncated": next_cursor < size,
        "reset": False,
    }


def _decode_output_page(data: bytes) -> tuple[str, bytes]:
    try:
        return data.decode("utf-8"), data
    except UnicodeDecodeError as exc:
        if exc.reason == "unexpected end of data":
            data = data[: exc.start]
        return data.decode("utf-8", errors="replace"), data


def _resume_supported(repo: Path, record) -> bool:
    from meow.cli.resume_cli import _request_problem, _validate
    from meow.cli.status_cli import RESUMABLE_SOURCES
    from meow.execution.run_state import TERMINAL_PHASES
    from meow.infrastructure.background import inspect_worker

    if record.source not in RESUMABLE_SOURCES or record.phase == "complete":
        return False
    if inspect_worker(record, include_terminal=True).state in {"running", "starting"}:
        return False
    if _validate(record, repo):
        return False
    problem = _request_problem(record, None, True)
    return problem is None or (
        record.phase in TERMINAL_PHASES
        and problem.startswith("Supply the intended outcome with --request:")
    )


def serialize_imported_run(repo: Path, run_id: str) -> dict:
    from meow.execution.run_state import TERMINAL_PHASES, RunStore
    from meow.infrastructure.background import inspect_worker

    store = RunStore(repo)
    record = store.load(run_id)
    saved_workflow = record.results.get("workflow")
    workflow = None
    if (
        isinstance(saved_workflow, dict)
        and isinstance(saved_workflow.get("name"), str)
        and isinstance(saved_workflow.get("scope"), str)
        and isinstance(saved_workflow.get("inputs", {}), dict)
    ):
        workflow = {
            "name": saved_workflow["name"],
            "scope": saved_workflow.get("scope"),
            "inputs": saved_workflow.get("inputs", {}),
        }
    try:
        output_available = run_output_path(repo, run_id).is_file()
    except (OSError, SessionError):
        output_available = False
    worker = inspect_worker(record, include_terminal=True)
    return {
        "id": record.id,
        "repo": str(Path(record.repo).resolve()),
        "source": record.source,
        "request": record.request,
        "phase": record.phase,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "worktree": record.worktree,
        "branch": record.branch,
        "feature_name": record.results.get("feature_name"),
        "workflow": workflow,
        "plan_file": record.plan_file,
        "background_output_available": output_available,
        "ui_session_id": record.ui_session_id,
        "ui_attempt_id": record.ui_attempt_id,
        "resume_supported": _resume_supported(repo, record),
        "cancel_supported": record.phase not in TERMINAL_PHASES
        and worker.state in {"running", "starting"},
    }


def list_imported_runs(repo: Path) -> list[dict]:
    from meow.execution.run_state import RunStore

    store = RunStore(repo)
    return [
        serialize_imported_run(repo, path.stem)
        for path in sorted(
            store.record_paths(),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
    ]


def _validate_imported_resume(  # ruff: ignore[complex-structure] -- keep API launch guards aligned with CLI resume
    repo: Path, run_id: str, request: str | None, repair: bool
):
    from meow.cli.resume_cli import (
        _request_problem,
        _required_session_roles,
        _resume_at,
        _validate,
    )
    from meow.cli.status_cli import RESUMABLE_SOURCES
    from meow.execution.run_state import RunStore
    from meow.infrastructure.background import inspect_worker

    record = RunStore(repo).load(run_id)
    if request is not None and (not isinstance(request, str) or "\x00" in request):
        raise SessionError("Resume request must be a string without NUL characters")
    if record.source not in RESUMABLE_SOURCES or record.phase == "complete":
        raise SessionError(f"Run {run_id} cannot be resumed")
    if inspect_worker(record, include_terminal=True).state in {"running", "starting"}:
        raise SessionError("Run worker is already active")
    if problem := _validate(record, repo):
        raise SessionError(f"Cannot resume: {problem}")
    if problem := _request_problem(record, request, True):
        raise SessionError(problem)
    resume_at = _resume_at(record)
    missing = _required_session_roles(record, resume_at) - set(record.sessions)
    if missing and not repair:
        raise SessionError("Cannot resume without saved Claude session references")
    return record


def _imported_resume_recipe(
    repo: Path, record, session_id: str, request: str | None
) -> Recipe:
    saved_workflow = record.results.get("workflow")
    inputs = (
        saved_workflow.get("inputs", {}) if isinstance(saved_workflow, dict) else {}
    )
    return Recipe(
        id=session_id,
        name=f"Resume {record.id[:8]}",
        repo=str(repo),
        mode="cli",
        command="run",
        request=request or record.request,
        workflow=saved_workflow.get("name")
        if isinstance(saved_workflow, dict)
        else None,
        inputs=inputs if isinstance(inputs, dict) else {},
        arguments=[],
        options={
            "unattended": bool(
                record.delivery.get("unattended", False)
                if isinstance(record.delivery, dict)
                else False
            )
        },
    )


def _imported_resume_argv(
    record, repo: Path, request: str | None, repair: bool
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "meow",
        "resume",
        record.id,
        "--continue",
        "--work-dir",
        str(repo),
    ]
    if request:
        argv.extend(["--request", request])
    if repair:
        argv.append("--repair")
    return argv


def resume_imported_run(
    repo: Path, run_id: str, request: str | None = None, repair: bool = False
) -> tuple[Session, Attempt]:
    from meow.ui.sessions import new_id

    repo = Path(repo).resolve()
    record = _validate_imported_resume(repo, run_id, request, repair)

    session_id = new_id()
    sessions = SessionStore(repo)
    recipe = _imported_resume_recipe(repo, record, session_id, request)
    session = sessions.save_recipe(recipe)
    with sessions.claim(session.id, "resume"):
        attempt = sessions.create_attempt(session.id, recipe, secrets.token_hex(16))
        attempt = sessions.update_attempt(
            session.id,
            attempt.id,
            argv=_imported_resume_argv(record, repo, request, repair),
            run_ids=[record.id],
        )
        attempt = _launch_owned_attempt(sessions, attempt)
    return session, attempt


def cancel_imported_run(repo: Path, run_id: str):
    from meow.execution.run_state import TERMINAL_PHASES, RunStore
    from meow.infrastructure.background import inspect_worker
    from meow.infrastructure.cancellation import request_cancel

    store = RunStore(repo)
    record = store.load(run_id)
    if record.phase in TERMINAL_PHASES or inspect_worker(
        record, include_terminal=True
    ).state not in {"running", "starting"}:
        raise SessionError(f"Run {run_id} is not active")
    return request_cancel(store, run_id)


def _signal_owned_tree(pid: int, identity: str, *, force: bool = True) -> bool:
    if process_identity(pid) != identity:
        return False
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F" if force else "/T"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def stop_attempt(repo: Path, session_id: str, attempt_id: str) -> Attempt:
    store = SessionStore(repo)
    with store.claim(session_id, "stop"):
        attempt = store._read_attempt(session_id, attempt_id)
        if attempt.state not in {"starting", "running", "stopping"}:
            return attempt
        pid = attempt.worker.get("pid")
        identity = attempt.worker.get("identity")
        if (
            not isinstance(pid, int)
            or not isinstance(identity, str)
            or process_identity(pid) != identity
        ):
            return store.finish_attempt(
                session_id, attempt_id, "interrupted", attempt.exit_code
            )
        marker = store._directory(session_id) / f"{attempt_id}.stop"
        marker.touch(exist_ok=True)
        attempt = store.update_attempt(session_id, attempt_id, state="stopping")
        deadline = time.monotonic() + _STOP_GRACE_SECONDS + 5
        while time.monotonic() < deadline:
            attempt = store._read_attempt(session_id, attempt_id)
            if attempt.state not in {"starting", "running", "stopping"}:
                return attempt
            time.sleep(0.05)
        child_pid = attempt.worker.get("child_pid")
        child_identity = attempt.worker.get("child_identity")
        if isinstance(child_pid, int) and isinstance(child_identity, str):
            _signal_owned_tree(child_pid, child_identity)
        stopped = _signal_owned_tree(pid, identity)
        return store.finish_attempt(
            session_id,
            attempt_id,
            "stopped" if stopped else "interrupted",
            attempt.exit_code,
        )


def resume_argv(
    attempt: Attempt, request: str | None = None, repair: bool = False
) -> list[str]:
    """Build a server-owned resume command; never accepted as a public recipe."""
    repo = Path(attempt.effective_recipe["repo"])
    run_ids = attempt.run_ids
    if run_ids:
        argv = [sys.executable, "-m", "meow", "resume", run_ids[-1], "--continue"]
        if request:
            argv.extend(["--request", request])
        if repair:
            argv.append("--repair")
        return argv
    if attempt.claude_session_id:
        from meow.ui.sessions import claude_path

        executable = claude_path()
        if not executable:
            raise SessionError(
                "Claude Code is not installed or is not available on PATH"
            )
        original = _recipe(attempt.effective_recipe)
        argv = command_for(original)
        prompt_index = argv.index("--")
        prompt = (
            f"{original.command}\n\n{request or original.request}"
            if original.command.startswith("/")
            else request or original.request
        )
        return [
            executable,
            "--resume",
            attempt.claude_session_id,
            *argv[1:prompt_index],
            "--",
            prompt + "\n\nWork unattended. Do not wait for human input.",
        ]
    del repo, request, repair
    raise SessionError("Attempt has no resumable MEOW run or Claude session")


def resume_attempt(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    repo: Path,
    session_id: str,
    attempt_id: str,
    request: str | None = None,
    repair: bool = False,
) -> Attempt:
    store = SessionStore(repo)
    with store.claim(session_id, "resume"):
        old = store._read_attempt(session_id, attempt_id)
        argv = resume_argv(old, request, repair)
        recipe = _recipe(old.effective_recipe)
        attempt = store.create_attempt(session_id, recipe, secrets.token_hex(16))
        store.update_attempt(session_id, attempt.id, argv=argv)
        # Resume argv is server-owned and stored on the new attempt.
        return _launch_owned_attempt(store, attempt)


def _start_owned_worker(store: SessionStore, attempt: Attempt) -> subprocess.Popen:
    token = attempt.worker["token"]
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if sys.platform == "win32"
        else 0
    )
    try:
        process = subprocess.Popen(
            _worker_argv(store.repo, attempt.session_id, attempt.id, token),
            cwd=store.repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_worker_env(),
            start_new_session=sys.platform != "win32",
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as exc:
        error = f"Worker launch failed: {exc}"
        store.finish_attempt(attempt.session_id, attempt.id, "failed", None)
        store.update_attempt(attempt.session_id, attempt.id, error=error)
        raise SessionError(f"Could not launch session worker: {exc}") from exc
    return process


def _wait_for_worker_registration(
    store: SessionStore, attempt: Attempt, process: subprocess.Popen
) -> Attempt:
    deadline = time.monotonic() + _REGISTER_SECONDS
    while time.monotonic() < deadline:
        current = store._read_attempt(attempt.session_id, attempt.id)
        if current.worker.get("pid"):
            return current
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        identity = process_identity(process.pid)
        if identity:
            _signal_owned_tree(process.pid, identity)
    store.finish_attempt(attempt.session_id, attempt.id, "failed", None)
    store.update_attempt(
        attempt.session_id, attempt.id, error="Worker registration timed out"
    )
    raise SessionError("Session worker did not register before timeout")


def _launch_owned_attempt(store: SessionStore, attempt: Attempt) -> Attempt:
    process = _start_owned_worker(store, attempt)
    return _wait_for_worker_registration(store, attempt, process)


def _render_claude_line(  # ruff: ignore[complex-structure, too-many-arguments, too-many-positional-arguments, too-many-branches, too-many-statements]
    store: SessionStore, session_id: str, attempt_id: str, line: bytes, shown: list[str]
) -> None:
    store.append_raw(session_id, attempt_id, line)
    try:
        event = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    typ = event.get("type")
    if isinstance(event.get("session_id"), str):
        store.update_attempt(
            session_id, attempt_id, claude_session_id=event["session_id"]
        )
    rendered = []
    if typ == "stream_event":
        delta = event.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            shown.append(delta["text"])
            rendered.append(delta["text"])
    elif typ in {"assistant", "user"}:
        message = event.get("message", {})
        for block in message.get("content", []):
            if block.get("type") == "tool_use":
                rendered.append(f"\n[tool: {block.get('name', 'unknown')}]\n")
            elif block.get("type") == "tool_result":
                rendered.append("\n[tool result]\n")
    elif typ == "result":
        result = event.get("result")
        if isinstance(result, str) and result:
            partial = "".join(shown)
            rendered.append(
                result[len(partial) :]
                if partial and result.startswith(partial)
                else ""
                if partial.endswith(result)
                else result
            )
        if event.get("subtype") == "error_max_turns":
            store.update_attempt(session_id, attempt_id, state="needs_decision")
    if event.get("permission_denials"):
        rendered.append("\n[permission denied]\n")
    if rendered:
        store.append_output(session_id, attempt_id, "".join(rendered).encode("utf-8"))


def worker_main(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements, too-many-locals]
    repo: Path, session_id: str, attempt_id: str, token: str
) -> int:
    store = SessionStore(repo)
    attempt = store._read_attempt(session_id, attempt_id)
    if attempt.worker.get("token") != token:
        return 2
    worker_identity = process_identity(os.getpid())
    if not worker_identity:
        store.finish_attempt(session_id, attempt_id, "failed", None)
        return 2
    recipe = _recipe(attempt.effective_recipe)
    env = os.environ.copy()
    env.update({
        "MEOW_UI_SESSION_ID": session_id,
        "MEOW_UI_ATTEMPT_ID": attempt_id,
        "MEOW_UI_REPO": str(Path(repo).resolve()),
    })
    claude = recipe.mode == "claude"
    store.update_attempt(
        session_id,
        attempt_id,
        state="running",
        started_at=_now_worker(),
        worker={**attempt.worker, "pid": os.getpid(), "identity": worker_identity},
    )
    child = None
    try:
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0
        )
        child = subprocess.Popen(
            attempt.argv,
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=sys.platform != "win32",
            creationflags=creationflags,
        )
        child_identity = process_identity(child.pid)
        store.update_attempt(
            session_id,
            attempt_id,
            worker={
                **store._read_attempt(session_id, attempt_id).worker,
                "child_pid": child.pid,
                "child_identity": child_identity,
            },
        )
        shown: list[str] = []
        chunks: queue.Queue[bytes | None] = queue.Queue()

        def read_child() -> None:
            assert child is not None and child.stdout is not None
            if claude:
                for output_line in iter(child.stdout.readline, b""):
                    chunks.put(output_line)
            else:
                while output_chunk := child.stdout.read1(4096):
                    chunks.put(output_chunk)
            chunks.put(None)

        threading.Thread(target=read_child, daemon=True).start()
        stream_ended = False
        while True:
            try:
                line = chunks.get(timeout=0.1)
            except queue.Empty:
                line = b""
            if line is None:
                stream_ended = True
            elif line:
                if claude:
                    _render_claude_line(store, session_id, attempt_id, line, shown)
                else:
                    store.append_output(session_id, attempt_id, line)
            if stream_ended and child.poll() is not None:
                break
            if (store._directory(session_id) / f"{attempt_id}.stop").exists():
                linked = set(attempt.run_ids) | set(
                    _linked_run_ids(Path(repo), session_id, attempt_id)
                )
                for run_id in linked:
                    with suppress(Exception):
                        from meow.execution.run_state import RunStore
                        from meow.infrastructure.cancellation import request_cancel

                        request_cancel(RunStore(repo), run_id)
                time.sleep(_STOP_GRACE_SECONDS)
                if child.poll() is None and child_identity:
                    _signal_owned_tree(child.pid, child_identity)
                break
        code = child.wait()
        run_ids = sorted(
            set(store._read_attempt(session_id, attempt_id).run_ids)
            | set(_linked_run_ids(Path(repo), session_id, attempt_id))
        )
        if run_ids:
            store.update_attempt(session_id, attempt_id, run_ids=run_ids)
        stop_requested = (store._directory(session_id) / f"{attempt_id}.stop").exists()
        current_state = store._read_attempt(session_id, attempt_id).state
        state = (
            "stopped"
            if stop_requested
            else "needs_decision"
            if current_state == "needs_decision"
            else "succeeded"
            if code == 0
            else "failed"
        )
        store.finish_attempt(session_id, attempt_id, state, code)
        return code
    except BaseException as exc:
        if child and child.poll() is None:
            with suppress(Exception):
                child.kill()
        store.update_attempt(session_id, attempt_id, error=f"Worker failed: {exc}")
        store.finish_attempt(session_id, attempt_id, "failed", None)
        return 1


def _now_worker() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()

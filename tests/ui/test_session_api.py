from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from pathlib import Path

import pytest

from meow.execution.run_state import RunStore
from meow.infrastructure import ui_sessions
from meow.project import repos as repos_module
from meow.project.custom.sources import builtin_dir
from meow.ui import session_api
from meow.ui.server import _Server
from meow.ui.sessions import Recipe, SessionStore

LAUNCH_PID = 123


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".meow" / "workflows").mkdir(parents=True)
    shutil.copy(
        builtin_dir() / "workflows" / "quick.toml",
        tmp_path / ".meow" / "workflows" / "quick.toml",
    )
    return tmp_path


@pytest.fixture
def running(project):
    server = _Server(0, Path(project))
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def post(port, path, payload, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(
        "POST",
        path,
        body=json.dumps(payload),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, json.loads(body) if body else None


def get(port, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request("GET", path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    status, content_type, disposition = (
        response.status,
        response.getheader("Content-Type"),
        response.getheader("Content-Disposition"),
    )
    connection.close()
    return status, content_type, disposition, body


def recipe(repo, **changes):
    value = {
        "name": "api-check",
        "mode": "cli",
        "command": "run",
        "request": "do the task",
        "workflow": None,
        "inputs": {},
        "arguments": [],
        "options": {},
    }
    return {**value, **changes}


def test_cli_options_are_derived_from_the_command_parsers(running):
    status, _, _, body = get(running.server_port, "/api/cli-options")
    assert status == HTTPStatus.OK
    commands = json.loads(body)["commands"]
    assert set(commands) == {"run", "plan", "review"}
    run = {item["flag"]: item for item in commands["run"]}
    assert run["--test"]["kind"] == "switch"
    assert run["--resume-at"]["choices"] == ["generate", "review"]
    assert run["--jira"]["nargs"] == "*"
    assert run["--from"]["aliases"] == ["--from", "-b"]
    assert run["--work-dir"]["controlled"]
    assert run["--background"]["controlled"]
    assert run["--name"]["controlled"]
    assert run["--no-worktree"]["controlled"]
    assert run["--lint-fix"]["controlled"]
    assert run["--resume-at"]["controlled"]
    assert run["--jira"]["controlled"] is None
    orchestrate = run
    assert orchestrate["--max-parallel"]["kind"] == "number"
    assert orchestrate["--orchestrate"]["aliases"] == ["--orchestrate", "-o"]
    assert orchestrate["--dry-run"]["help"]


def test_cli_options_hide_legacy_modes_and_expose_in_place_delivery():
    from meow.ui.cli_options import cli_options

    run = {item["flag"]: item for item in cli_options()["commands"]["run"]}
    for flag in ("--no-worktree-commit", "--no-worktree-push"):
        assert run[flag]["kind"] == "switch"
        assert run[flag]["controlled"] is None


def test_cli_options_include_new_parser_flags_without_a_ui_flag_list(monkeypatch):
    from meow.ui import cli_options
    from meow.ui.sessions import _parser_for

    parser = cli_options._build_arg_parser()
    _parser_for(parser, "run").add_argument("--extra-check", choices=["quick", "full"])
    monkeypatch.setattr(cli_options, "_build_arg_parser", lambda: parser)
    flags = cli_options.cli_options()["commands"]["run"]
    extra = next(item for item in flags if item["flag"] == "--extra-check")
    assert extra["choices"] == ["quick", "full"]
    assert extra["controlled"] is None


def test_orchestrate_preview_builds_cli_and_warns_about_delivery(running, project):
    status, body = post(
        running.server_port,
        "/api/sessions/preview",
        {
            "repo": str(project),
            "recipe": recipe(
                project,
                command="run",
                options={},
                arguments=[
                    "--orchestrate",
                    "--max-parallel",
                    "2",
                    "--dry-run",
                    "--no-pr",
                ],
            ),
        },
    )
    assert status == HTTPStatus.OK, body
    assert body["argv"][4:] == [
        "run",
        "--orchestrate",
        "--max-parallel",
        "2",
        "--dry-run",
        "--no-pr",
        "--",
        "do the task",
    ]
    assert body["warnings"]


@pytest.mark.parametrize("mode", [[], {}])
def test_preview_rejects_unhashable_mode_without_dropping_connection(
    running, project, mode
):
    status, body = post(
        running.server_port,
        "/api/sessions/preview",
        {"repo": str(project), "recipe": recipe(project, mode=mode)},
    )
    assert status == HTTPStatus.BAD_REQUEST
    assert body["error"] == "mode must be 'cli' or 'claude'"


def test_schedule_session_freezes_recipe_lists_and_cancels(  # ruff: ignore[too-many-statements]
    running, project, monkeypatch
):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import Mock

    from meow.infrastructure import scheduling

    monkeypatch.setattr(scheduling, "platform", "linux")
    monkeypatch.setattr("meow.ui.sessions.process_identity", lambda _pid: "test-owner")
    monkeypatch.setattr(scheduling, "_unit_dir", lambda: project / "units")
    monkeypatch.setattr(
        scheduling.subprocess, "run", Mock(return_value=Mock(stdout=""))
    )
    status, body = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project), "recipe": recipe(project)},
    )
    assert status == HTTPStatus.CREATED, body
    session_id = body["session"]["id"]

    def scheduler_command(argv, **_kwargs):
        if argv[:3] == ["systemctl", "--user", "enable"]:
            # An imminent trigger must be able to acquire its session claim.
            with SessionStore(project).claim(session_id, "launch"):
                pass
        return Mock(stdout="")

    monkeypatch.setattr(scheduling.subprocess, "run", scheduler_command)
    at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    status, body = post(
        running.server_port,
        f"/api/sessions/{session_id}/schedule",
        {"repo": str(project), "at": at},
    )
    assert status == HTTPStatus.CREATED, body
    assert body["session"]["state"] == "scheduled"
    schedule_id = body["schedule"]["id"]
    frozen = scheduling.list_schedules(project)[0]["recipe"]
    assert frozen["request"] == "do the task"
    assert body["session"]["schedules"][0]["id"] == schedule_id
    assert body["session"]["attempts"] == []
    status, body = post(
        running.server_port,
        f"/api/sessions/{session_id}/edit-schedule",
        {
            "repo": str(project),
            "schedule_id": schedule_id,
            "at": "2099-12-01T15:30+03:00",
        },
    )
    assert status == HTTPStatus.OK, body
    assert body["session"]["schedules"][0]["at"] == "2099-12-01T12:30:00+00:00"
    assert scheduling.list_schedules(project)[0]["recipe"] == frozen
    status, _ = post(
        running.server_port,
        f"/api/sessions/{session_id}/edit-schedule",
        {"repo": str(project), "schedule_id": "f" * 32, "at": at},
    )
    assert status == HTTPStatus.NOT_FOUND
    status, _ = post(
        running.server_port,
        f"/api/sessions/{session_id}/edit-schedule",
        {"repo": str(project), "schedule_id": schedule_id, "at": "yesterday"},
    )
    assert status == HTTPStatus.BAD_REQUEST
    status, body = post(
        running.server_port,
        f"/api/sessions/{session_id}/cancel-schedule",
        {"repo": str(project), "schedule_id": schedule_id},
    )
    assert status == HTTPStatus.OK
    assert body["session"]["schedules"][0]["state"] == "cancelled"


def test_schedule_rejects_invalid_time_and_foreign_repo(running, project):
    status, body = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project), "recipe": recipe(project)},
    )
    session_id = body["session"]["id"]
    for payload in (
        {"repo": str(project), "at": "yesterday"},
        {"repo": "/unknown", "at": "20:30"},
    ):
        status, _ = post(
            running.server_port, f"/api/sessions/{session_id}/schedule", payload
        )
        assert status == HTTPStatus.BAD_REQUEST


def test_create_list_detail_and_update_session(running, project):
    status, body = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project.resolve()), "recipe": recipe(project)},
    )
    assert status == HTTPStatus.CREATED
    created = body["session"]
    assert created["recipe"]["mode"] == "cli"
    assert created["attempts"] == []
    assert created["attempt_count"] == 0
    assert "token" not in json.dumps(created)

    status, _, _, body = get(
        running.server_port, f"/api/sessions?repo={project.resolve()}"
    )
    assert status == HTTPStatus.OK
    assert json.loads(body)["sessions"][0]["id"] == created["id"]

    status, _, _, body = get(
        running.server_port,
        f"/api/sessions/{created['id']}?repo={project.resolve()}",
    )
    assert status == HTTPStatus.OK
    assert json.loads(body)["session"]["recipe"]["request"] == "do the task"

    status, body = _put(
        running.server_port,
        f"/api/sessions/{created['id']}",
        {"repo": str(project.resolve()), "recipe": recipe(project, request="edited")},
    )
    assert status == HTTPStatus.OK
    assert body["session"]["recipe"]["request"] == "edited"


def test_session_detail_read_reconciles_a_dead_saved_worker(
    running, project, monkeypatch
):
    store = SessionStore(project)
    saved = store.save_recipe(
        Recipe(
            "a" * 32,
            "stale",
            str(project.resolve()),
            "cli",
            "run",
            "task",
            None,
            {},
            [],
            {},
        )
    )
    attempt = store.create_attempt(saved.id, Recipe(**saved.recipe), "token")
    store.update_attempt(
        saved.id,
        attempt.id,
        state="running",
        worker={"pid": 43210, "identity": "dead-worker", "token": "token"},
    )
    monkeypatch.setattr(ui_sessions, "process_identity", lambda _pid: None)

    status, body = session_api.get(
        running,
        f"/api/sessions/{saved.id}?repo={project.resolve()}",
    )

    assert status == HTTPStatus.OK
    assert body["session"]["attempts"][0]["state"] == "interrupted"

    fresh = store.save_recipe(
        Recipe(
            "c" * 32,
            "starting",
            str(project.resolve()),
            "cli",
            "run",
            "task",
            None,
            {},
            [],
            {},
        )
    )
    startup = store.create_attempt(fresh.id, Recipe(**fresh.recipe), "token")
    attempt_path = store._directory(fresh.id) / f"{startup.id}.json"
    attempt_data = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt_data["created_at"] = "2000-01-01T00:00:00+00:00"
    attempt_path.write_text(json.dumps(attempt_data), encoding="utf-8")
    claim_path = store._directory(fresh.id) / "claim.json"
    claim_path.write_text(
        json.dumps({"action": "launch", "pid": LAUNCH_PID, "identity": "launch-owner"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ui_sessions,
        "process_identity",
        lambda pid: "launch-owner" if pid == LAUNCH_PID else None,
    )
    status, body = session_api.get(
        running,
        f"/api/sessions/{fresh.id}?repo={project.resolve()}",
    )
    assert status == HTTPStatus.OK
    assert body["session"]["attempts"][0]["state"] == "starting"


def test_archived_session_launch_is_rejected_by_api(running, project):
    store = SessionStore(project)
    saved = store.save_recipe(
        Recipe(
            "b" * 32,
            "archived",
            str(project.resolve()),
            "cli",
            "run",
            "task",
            None,
            {},
            [],
            {},
        )
    )
    store.archive(saved.id, True)

    status, body = post(
        running.server_port,
        f"/api/sessions/{saved.id}/launch",
        {"repo": str(project.resolve())},
    )

    assert status == HTTPStatus.CONFLICT
    assert "Unarchive" in body["error"]


def _put(port, path, payload):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(
        "PUT",
        path,
        body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()
    return response.status, body


def test_preview_capabilities_and_output_download(running, project):  # ruff: ignore[too-many-statements]
    status, preview = post(
        running.server_port,
        "/api/sessions/preview",
        {"repo": str(project.resolve()), "recipe": recipe(project)},
    )
    assert status == HTTPStatus.OK
    assert preview["valid"] is True
    assert preview["argv"]
    assert preview["capabilities"]["cli"]["available"] is True
    assert isinstance(preview["capabilities"]["claude"]["available"], bool)

    status, _, _, body = get(
        running.server_port, f"/api/capabilities?repo={project.resolve()}"
    )
    assert status == HTTPStatus.OK
    assert json.loads(body)["cli"]["available"] is True

    status, created = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project.resolve()), "recipe": recipe(project)},
    )
    session = created["session"]
    store = SessionStore(project)
    attempt = store.create_attempt(
        session["id"], Recipe(**store.load(session["id"]).recipe), "private-token"
    )
    store.append_output(session["id"], attempt.id, b"hello output\n")
    status, _, _, body = get(
        running.server_port,
        f"/api/sessions/{session['id']}/output?repo={project.resolve()}&attempt={attempt.id}&cursor=0&limit=5",
    )
    assert status == HTTPStatus.OK
    assert json.loads(body)["text"] == "hello"
    status, content_type, disposition, body = get(
        running.server_port,
        f"/api/sessions/{session['id']}/download?repo={project.resolve()}&attempt={attempt.id}",
    )
    assert status == HTTPStatus.OK
    assert content_type.startswith("text/plain")
    assert "attachment" in disposition
    assert body == b"hello output\n"


def test_session_serializers_use_exact_public_fields(running, project):
    status, body = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project.resolve()), "recipe": recipe(project)},
    )
    session_id = body["session"]["id"]
    summary_fields = {
        "id",
        "name",
        "repo",
        "state",
        "mode",
        "command",
        "created_at",
        "updated_at",
        "archived",
        "active_attempt_id",
        "attempt_count",
        "last_attempt",
        "schedules",
    }
    assert set(body["session"]) == summary_fields | {"recipe", "attempts"}
    assert set(body["session"]["recipe"]) == {
        "id",
        "name",
        "repo",
        "mode",
        "command",
        "request",
        "workflow",
        "inputs",
        "arguments",
        "options",
    }

    store = SessionStore(project)
    store.create_attempt(
        session_id, Recipe(**store.load(session_id).recipe), "private-token"
    )
    attempt = store._read_attempt(session_id, store.load(session_id).attempts[0])
    store.update_attempt(
        session_id,
        attempt.id,
        error="failed at C:\\private\\claude.exe and /tmp/private/file",
    )
    status, _, _, response = get(
        running.server_port,
        f"/api/sessions/{session_id}?repo={project.resolve()}",
    )
    detail = json.loads(response)["session"]
    assert set(detail["attempts"][0]) == {
        "id",
        "state",
        "created_at",
        "started_at",
        "finished_at",
        "effective_recipe",
        "exit_code",
        "output_size",
        "claude_session_id",
        "run_ids",
        "error",
        "worker_alive",
        "resume_supported",
        "resume_needs_request",
    }
    assert detail["attempts"][0]["error"] == "failed at [path] and [path]"
    assert "private-token" not in json.dumps(detail)
    assert status == HTTPStatus.OK


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "private_path", [r"C:\private data\file.db", "/srv/private data/file.db"]
)
def test_filesystem_error_responses_redact_server_paths(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    running, project, monkeypatch, method, private_path
):
    failure = PermissionError(f"permission denied: {private_path}")
    if method == "get":
        monkeypatch.setattr(
            session_api, "get", lambda *_args: (_ for _ in ()).throw(failure)
        )
        status, _, _, body = get(running.server_port, "/api/sessions")
    else:
        monkeypatch.setattr(
            session_api,
            "post",
            lambda *_args: (_ for _ in ()).throw(failure),
        )
        status, body = post(
            running.server_port,
            "/api/sessions",
            {"repo": str(project.resolve()), "recipe": recipe(project)},
        )
        body = json.dumps(body).encode("utf-8")

    message = json.loads(body)["error"]
    assert status == HTTPStatus.BAD_REQUEST
    assert "file.db" not in message
    assert "permission denied" in message.lower()


def test_output_cursor_clamps_and_resets_after_truncation(running, project):
    _, body = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project.resolve()), "recipe": recipe(project)},
    )
    session_id = body["session"]["id"]
    store = SessionStore(project)
    attempt = store.create_attempt(
        session_id, Recipe(**store.load(session_id).recipe), "private-token"
    )
    store.append_output(session_id, attempt.id, b"abcdef")
    url = (
        f"/api/sessions/{session_id}/output?repo={project.resolve()}"
        f"&attempt={attempt.id}"
    )
    status, _, _, output = get(running.server_port, url + "&cursor=999&limit=999999")
    assert status == HTTPStatus.OK
    assert json.loads(output)["text"] == ""
    (store._directory(session_id) / attempt.output_file).write_bytes(b"short")
    status, _, _, output = get(running.server_port, url + "&cursor=6&limit=20")
    page = json.loads(output)
    assert status == HTTPStatus.OK
    assert page["reset"] is True
    assert page["text"] == "short"


def test_mutations_enforce_origin_body_and_repo_allowlist(running, project, tmp_path):
    payload = {"repo": str(project.resolve()), "recipe": recipe(project)}
    status, _ = post(
        running.server_port,
        "/api/sessions",
        payload,
        {"Origin": "https://attacker.example"},
    )
    assert status == HTTPStatus.FORBIDDEN
    outsider = tmp_path / "not-registered"
    outsider.mkdir()
    status, _ = post(
        running.server_port, "/api/sessions", payload | {"repo": str(outsider)}
    )
    assert status == HTTPStatus.BAD_REQUEST

    connection = http.client.HTTPConnection("127.0.0.1", running.server_port)
    connection.request(
        "POST",
        "/api/sessions",
        body=json.dumps(payload),
        headers={"Host": "attacker.example", "Content-Type": "application/json"},
    )
    assert connection.getresponse().status == HTTPStatus.FORBIDDEN
    connection.close()

    connection = http.client.HTTPConnection("127.0.0.1", running.server_port)
    connection.request(
        "POST",
        "/api/sessions",
        body=" " * (64 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert connection.getresponse().status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    connection.close()
    assert SessionStore(project).list() == []


@pytest.mark.parametrize("session_id", ["../runs", "C:%5Ctemp%5Cx", "not-a-uuid"])
def test_api_rejects_unsafe_session_ids(running, session_id):
    status, _, _, _ = get(
        running.server_port,
        f"/api/sessions/{session_id}/output?repo={running.working_dir}",
    )
    assert status in {HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND}


def test_imported_run_apis_aggregate_registered_repositories(  # ruff: ignore[too-many-statements]
    running, project, tmp_path, monkeypatch
):
    repo2 = tmp_path / "other"
    repo2.mkdir()
    monkeypatch.setattr(
        repos_module, "_saved", lambda: [str(project.resolve()), str(repo2.resolve())]
    )
    session_repos = {str(project.resolve()), str(repo2.resolve())}
    for repo, with_output in ((project, True), (repo2, False)):
        status, _ = post(
            running.server_port,
            "/api/sessions",
            {"repo": str(repo.resolve()), "recipe": recipe(repo)},
        )
        assert status == HTTPStatus.CREATED
        store = RunStore(repo)
        record = store.create(
            source="prompt",
            request="known request",
            repo=repo,
            worktree=repo,
            branch="main",
        )
        if with_output:
            store.directory.joinpath(f"{record.id}.log").write_text(
                "known log", encoding="utf-8"
            )

    status, _, _, body = get(running.server_port, "/api/runs")
    assert status == HTTPStatus.OK
    imported = json.loads(body)["runs"]
    assert {item["repo"] for item in imported} == {
        str(project.resolve()),
        str(repo2.resolve()),
    }
    assert {item["background_output_available"] for item in imported} == {True, False}
    assert all("argv" not in item for item in imported)

    status, _, _, body = get(running.server_port, "/api/sessions")
    assert status == HTTPStatus.OK
    assert {item["repo"] for item in json.loads(body)["sessions"]} == session_repos

    status, _, _, body = get(running.server_port, f"/api/runs?repo={project.resolve()}")
    assert status == HTTPStatus.OK
    assert [item["repo"] for item in json.loads(body)["runs"]] == [
        str(project.resolve())
    ]

    run_id = next(
        item["id"] for item in imported if item["repo"] == str(project.resolve())
    )
    status, _, _, body = get(
        running.server_port,
        f"/api/runs/{run_id}/output?repo={project.resolve()}&cursor=0&limit=4",
    )
    assert status == HTTPStatus.OK
    assert json.loads(body)["text"] == "know"
    status, content_type, disposition, body = get(
        running.server_port,
        f"/api/runs/{run_id}/download?repo={project.resolve()}",
    )
    assert status == HTTPStatus.OK
    assert content_type.startswith("text/plain")
    assert "attachment" in disposition
    assert body == b"known log"


def test_imported_run_resume_and_cancel_are_adapted_to_http(
    running, project, monkeypatch
):
    subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
    store = RunStore(project)
    resumable = store.create(
        source="prompt",
        request="resume this",
        repo=project,
        worktree=project,
        branch="main",
    )
    store.transition(resumable.id, "planned")
    monkeypatch.setattr(
        ui_sessions, "_launch_owned_attempt", lambda _store, attempt: attempt
    )
    status, body = post(
        running.server_port,
        f"/api/runs/{resumable.id}/resume",
        {"repo": str(project.resolve()), "repair": True},
    )
    assert status == HTTPStatus.ACCEPTED, body
    session_id = body["session"]["id"]
    attempt_id = body["attempt"]["id"]
    attempt = SessionStore(project)._read_attempt(session_id, attempt_id)
    assert attempt.run_ids == [resumable.id]
    assert body["session"]["attempts"][0]["id"] == attempt_id
    assert "private-token" not in json.dumps(body)

    active = store.create(
        source="prompt", request="active", repo=project, worktree=project, branch="main"
    )
    store.transition(active.id, "planning")
    store.update_background(active.id, launch_started_at=time.time())
    status, body = post(
        running.server_port,
        f"/api/runs/{active.id}/cancel",
        {"repo": str(project.resolve())},
    )
    assert status == HTTPStatus.OK
    assert body["run"]["id"] == active.id
    assert Path(store.directory / f"{active.id}.cancel").exists()


def test_session_lifecycle_launch_stop_duplicate_archive(running, project, monkeypatch):  # ruff: ignore[too-many-statements]
    from meow.infrastructure import ui_sessions

    monkeypatch.setattr(
        ui_sessions, "_launch_owned_attempt", lambda _store, attempt: attempt
    )
    _, created = post(
        running.server_port,
        "/api/sessions",
        {"repo": str(project.resolve()), "recipe": recipe(project)},
    )
    session_id = created["session"]["id"]
    status, body = post(
        running.server_port,
        f"/api/sessions/{session_id}/launch",
        {"repo": str(project.resolve())},
    )
    assert status == HTTPStatus.OK
    attempt_id = body["attempt"]["id"]
    assert body["session"]["active_attempt_id"] == attempt_id

    status, body = post(
        running.server_port,
        f"/api/sessions/{session_id}/stop",
        {"repo": str(project.resolve())},
    )
    assert status == HTTPStatus.OK
    assert body["attempt"]["state"] == "interrupted"

    status, body = post(
        running.server_port,
        f"/api/sessions/{session_id}/duplicate",
        {"repo": str(project.resolve())},
    )
    assert status == HTTPStatus.CREATED
    duplicate_id = body["session"]["id"]
    assert duplicate_id != session_id
    assert body["session"]["attempts"] == []

    status, body = post(
        running.server_port,
        f"/api/sessions/{duplicate_id}/archive",
        {"repo": str(project.resolve())},
    )
    assert status == HTTPStatus.OK
    assert body["session"]["archived"] is True
    status, body = post(
        running.server_port,
        f"/api/sessions/{duplicate_id}/unarchive",
        {"repo": str(project.resolve())},
    )
    assert status == HTTPStatus.OK
    assert body["session"]["archived"] is False


def test_claude_agent_recipe_requires_verified_markdown_name(project, monkeypatch):
    from meow.ui import sessions

    agent_dir = project / ".claude" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "helper.md").write_text(
        "---\nname: helper\ndescription: Help with tasks.\n---\nPrompt.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sessions, "claude_help", lambda: "Usage: claude [--agent <name>]"
    )
    assert sessions.claude_agent_names(project) == {"helper"}

    monkeypatch.setattr(sessions, "claude_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(
        sessions,
        "detect_capabilities",
        lambda _repo: {
            "available": True,
            "version": "test",
            "missing": [],
            "permission_policy": "dontAsk+none",
            "bypass_supported": True,
            "error": None,
        },
    )
    agent_recipe = recipe(
        project,
        mode="claude",
        command="prompt",
        options={"permission_policy": "default", "agent": "helper"},
    )
    validated = sessions.validate_recipe(project, agent_recipe)
    assert "--agent" in sessions.command_for(validated)
    assert (
        sessions.command_for(validated)[
            sessions.command_for(validated).index("--agent") + 1
        ]
        == "helper"
    )

    with pytest.raises(sessions.RecipeError, match="Claude Markdown agent"):
        sessions.validate_recipe(
            project,
            recipe(
                project,
                mode="claude",
                command="prompt",
                options={"agent": "toml-only-agent"},
            ),
        )

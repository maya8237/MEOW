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
from meow.ui.server import _Server
from meow.ui.sessions import Recipe, SessionStore


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
        "options": {"unattended": True},
    }
    return {**value, **changes}


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
    assert "capabilities" in preview

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
    }
    assert detail["attempts"][0]["error"] == "failed at [path] and [path]"
    assert "private-token" not in json.dumps(detail)
    assert status == HTTPStatus.OK


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

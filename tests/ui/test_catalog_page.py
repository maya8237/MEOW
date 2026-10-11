import http.client
import json
import shutil
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.request import urlopen

import pytest

from meow.project.custom.sources import builtin_dir
from meow.ui import create as create_module
from meow.ui import repos as repos_module
from meow.ui.catalog import build_catalog
from meow.ui.server import _Server


@pytest.fixture
def project(tmp_path):
    workflows = tmp_path / ".meow" / "workflows"
    workflows.mkdir(parents=True)
    shutil.copy(builtin_dir() / "workflows" / "quick.toml", workflows / "nightly.toml")

    agents = tmp_path / ".meow" / "agents"
    agents.mkdir(parents=True)
    shutil.copy(builtin_dir() / "agents" / "explorer.toml", agents / "triage.toml")

    skill = tmp_path / "tools" / "skills" / "notes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: notes\ndescription: Take project notes.\n---\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["tools/skills"]\n', encoding="utf-8"
    )
    return tmp_path


def test_catalog_lists_custom_and_builtin_definitions(project):
    catalog = build_catalog(project)

    assert catalog["error"] is None
    workflows = {item["name"]: item for item in catalog["workflows"]}
    assert workflows["nightly"]["scope"] == "project"
    assert workflows["nightly"]["steps"]
    assert "default" in workflows

    agents = {item["name"]: item for item in catalog["agents"]}
    assert agents["triage"]["scope"] == "project"
    assert "explorer" in agents

    skills = {item["name"]: item for item in catalog["skills"]}
    assert skills["notes"]["qualified_name"] == "meow-custom:notes"
    assert skills["notes"]["scope"] == "project"
    assert "Take project notes." in skills["notes"]["text"]


def test_catalog_reports_configuration_errors(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("not = [valid", encoding="utf-8")

    catalog = build_catalog(tmp_path)

    assert catalog["error"]
    assert catalog["workflows"] == []


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


def test_server_serves_page_and_catalog(running):
    base = f"http://127.0.0.1:{running.server_port}"

    with urlopen(f"{base}/") as response:
        assert b"MEOW catalog" in response.read()

    with urlopen(f"{base}/api/catalog") as response:
        assert b'"nightly"' in response.read()


def test_server_rejects_foreign_host_and_unknown_paths(running):
    connection = http.client.HTTPConnection("127.0.0.1", running.server_port)
    connection.request("GET", "/api/catalog", headers={"Host": "evil.example"})
    assert connection.getresponse().status == HTTPStatus.FORBIDDEN

    connection = http.client.HTTPConnection("127.0.0.1", running.server_port)
    connection.request("GET", "/etc/passwd")
    assert connection.getresponse().status == HTTPStatus.NOT_FOUND


def test_server_serves_logo(running):
    with urlopen(f"http://127.0.0.1:{running.server_port}/logo.png") as response:
        assert response.headers["Content-Type"] == "image/png"
        assert response.read(8) == b"\x89PNG\r\n\x1a\n"


def test_server_serves_code_font(running):
    url = f"http://127.0.0.1:{running.server_port}/fonts/JetBrainsMono-Regular.woff2"
    with urlopen(url) as response:
        assert response.headers["Content-Type"] == "font/woff2"
        assert response.read(4) == b"wOF2"


def _post(port, path, payload, headers=None):
    sent = {"Content-Type": "application/json", **(headers or {})}
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request("POST", path, body=json.dumps(payload), headers=sent)
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def test_create_agent_copies_existing_definition(running, project):
    status, body = _post(
        running.server_port,
        "/api/agents",
        {"name": "helper", "description": "Helps out.", "source": "explorer"},
    )

    assert status == HTTPStatus.CREATED
    created = project / ".meow" / "agents" / "helper.toml"
    assert body["file"] == str(created)
    text = created.read_text(encoding="utf-8")
    assert text.startswith('name = "helper"\ndescription = "Helps out."\n')
    assert "ejected_from" not in text
    agents = {item["name"]: item for item in build_catalog(project)["agents"]}
    assert agents["helper"]["scope"] == "project"


def test_create_workflow_copies_existing_definition(running, project):
    status, _ = _post(
        running.server_port,
        "/api/workflows",
        {"name": "nightly-copy", "description": "Copy.", "source": "nightly"},
    )

    assert status == HTTPStatus.CREATED
    workflows = {item["name"] for item in build_catalog(project)["workflows"]}
    assert "nightly-copy" in workflows


def test_create_skill_in_configured_folder(running, project):
    status, body = _post(
        running.server_port,
        "/api/skills",
        {"name": "journal", "description": "Keeps a journal.", "source": ""},
    )

    assert status == HTTPStatus.CREATED
    assert (project / "tools" / "skills" / "journal" / "SKILL.md") == Path(body["file"])
    skills = {item["name"]: item for item in build_catalog(project)["skills"]}
    assert skills["journal"]["description"] == "Keeps a journal."


def test_create_skill_copies_existing_skill(running, project):
    status, _ = _post(
        running.server_port,
        "/api/skills",
        {"name": "notes-copy", "description": "Copy.", "source": "notes"},
    )

    assert status == HTTPStatus.CREATED
    copied = project / "tools" / "skills" / "notes-copy" / "SKILL.md"
    assert copied.read_text(encoding="utf-8").startswith("---\nname: notes-copy\n")
    assert "Body." in copied.read_text(encoding="utf-8")


def test_create_skill_without_config_adds_default_folder(tmp_path):
    status, _ = _create(tmp_path, "skills", {"name": "fresh", "description": "New."})

    assert status == HTTPStatus.CREATED
    assert (tmp_path / ".meow" / "skills" / "fresh" / "SKILL.md").is_file()
    config = (tmp_path / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert '[custom]\nskills = [".meow/skills"]' in config
    assert "fresh" in {item["name"] for item in build_catalog(tmp_path)["skills"]}


def _create(project_dir, kind, payload):
    server = _Server(0, project_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return _post(server.server_port, f"/api/{kind}", payload)
    finally:
        server.shutdown()
        server.server_close()


def test_create_at_local_scope(running, project):
    status, _ = _post(
        running.server_port,
        "/api/agents",
        {
            "name": "localhelper",
            "description": "Local.",
            "source": "explorer",
            "scope": "local",
        },
    )

    assert status == HTTPStatus.CREATED
    assert (project / ".meow" / "local" / "agents" / "localhelper.toml").is_file()


def test_create_at_user_scope(running, project, tmp_path, monkeypatch):
    home = tmp_path / "home" / ".meow"
    monkeypatch.setattr(create_module, "user_config_path", lambda: home / "config.toml")

    status, _ = _post(
        running.server_port,
        "/api/workflows",
        {
            "name": "shared-flow",
            "description": "Shared.",
            "source": "nightly",
            "scope": "user",
        },
    )

    assert status == HTTPStatus.CREATED
    assert (home / "workflows" / "shared-flow.toml").is_file()


def test_create_user_skill_registers_user_folder(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".meow"
    monkeypatch.setattr(create_module, "user_config_path", lambda: home / "config.toml")
    project = tmp_path / "project"
    project.mkdir()

    status, _ = _create(
        project, "skills", {"name": "shared", "description": "Shared.", "scope": "user"}
    )

    assert status == HTTPStatus.CREATED
    assert (home / "skills" / "shared" / "SKILL.md").is_file()
    assert 'skills = ["skills"]' in (home / "config.toml").read_text(encoding="utf-8")


def test_create_in_chosen_repo(running, project, tmp_path, monkeypatch):
    monkeypatch.setattr(repos_module, "registry_path", lambda: tmp_path / "repos.toml")
    other = tmp_path / "other"
    other.mkdir()
    repos_module.remember(other)

    status, _ = _post(
        running.server_port,
        "/api/agents",
        {
            "name": "helper",
            "description": "Helps.",
            "source": "explorer",
            "repo": str(other.resolve()),
        },
    )

    assert status == HTTPStatus.CREATED
    assert (other / ".meow" / "agents" / "helper.toml").is_file()
    assert not (project / ".meow" / "agents" / "helper.toml").exists()


def test_create_rejects_repo_not_in_list(running, tmp_path, monkeypatch):
    monkeypatch.setattr(repos_module, "registry_path", lambda: tmp_path / "repos.toml")

    status, _ = _post(
        running.server_port,
        "/api/agents",
        {"name": "helper", "source": "explorer", "repo": str(tmp_path / "elsewhere")},
    )

    assert status == HTTPStatus.BAD_REQUEST


def test_create_rejects_unknown_scope(running):
    status, _ = _post(
        running.server_port,
        "/api/agents",
        {"name": "helper2", "source": "explorer", "scope": "global"},
    )

    assert status == HTTPStatus.BAD_REQUEST


def test_create_rejects_bad_requests(running, project):
    port = running.server_port
    status, body = _post(
        port, "/api/agents", {"name": "Bad Name", "source": "explorer"}
    )
    assert status == HTTPStatus.BAD_REQUEST
    assert "lowercase" in body["error"]

    status, _ = _post(port, "/api/agents", {"name": "triage", "source": "explorer"})
    assert status == HTTPStatus.CONFLICT

    status, _ = _post(port, "/api/agents", {"name": "fresh", "source": "missing"})
    assert status == HTTPStatus.BAD_REQUEST

    status, _ = _post(
        port,
        "/api/agents",
        {"name": "fresh", "source": "explorer"},
        {"Content-Type": "text/plain"},
    )
    assert status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE

    status, _ = _post(
        port,
        "/api/agents",
        {"name": "fresh", "source": "explorer"},
        {"Origin": "http://evil.example"},
    )
    assert status == HTTPStatus.FORBIDDEN

    assert not (project / ".meow" / "agents" / "fresh.toml").exists()

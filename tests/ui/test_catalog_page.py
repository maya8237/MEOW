import http.client
import shutil
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.request import urlopen

import pytest

from meow.project.custom.sources import builtin_dir
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

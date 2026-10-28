import http.client
import json
import re
import shutil
import subprocess
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.request import urlopen

import pytest

from meow.project import repos as repos_module
from meow.project.custom.sources import builtin_dir
from meow.ui import create as create_module
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
    hooks = tmp_path / ".meow" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "guard.toml").write_text(
        'description = "Guard edits"\nevent = "PreToolUse"\n'
        'matcher = "Write|Edit"\ncommand = "true"\n',
        encoding="utf-8",
    )
    (hooks / "mine.toml").write_text(
        'description = "Only generators"\nevent = "Stop"\n'
        'command = "true"\nroles = ["generator"]\n',
        encoding="utf-8",
    )
    return tmp_path


def test_catalog_lists_hooks_with_their_sync_state(project):
    hooks = {item["name"]: item for item in build_catalog(project)["hooks"]}

    assert hooks["guard"]["event"] == "PreToolUse"
    assert hooks["guard"]["matcher"] == "Write|Edit"
    assert hooks["guard"]["scope"] == "project"
    assert hooks["guard"]["synced"] is True
    assert hooks["mine"]["roles"] == ["generator"]
    assert hooks["mine"]["synced"] is False


def test_catalog_lists_custom_and_builtin_definitions(project):
    catalog = build_catalog(project)

    assert catalog["error"] is None
    workflows = {item["name"]: item for item in catalog["workflows"]}
    assert workflows["nightly"]["scope"] == "project"
    assert workflows["nightly"]["steps"]
    assert "default" in workflows

    agents = {item["name"]: item for item in catalog["agents"]}
    assert agents["triage"]["scope"] == "project"
    assert agents["triage"]["claude_resolvable"] is False
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
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
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


def test_server_serves_compiled_frontend_assets(running):
    base = f"http://127.0.0.1:{running.server_port}"
    page = urlopen(f"{base}/").read().decode()
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
    assert assets, "The catalog must load its bundled React frontend."
    for asset in assets:
        with urlopen(base + asset) as response:
            expected = "text/css" if asset.endswith(".css") else "text/javascript"
            assert response.headers["Content-Type"].startswith(expected)
            assert response.read()


def test_server_observes_new_assets_after_a_frontend_rebuild(
    running, tmp_path, monkeypatch
):
    from meow.ui import server as server_module

    monkeypatch.setattr(server_module, "_STATIC_DIR", tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "rebuilt.js").write_text("console.log('rebuilt');", encoding="utf-8")
    with urlopen(
        f"http://127.0.0.1:{running.server_port}/assets/rebuilt.js"
    ) as response:
        assert response.read() == b"console.log('rebuilt');"


@pytest.mark.parametrize("path", ["/assets/../server.py", "/assets/missing.js"])
def test_server_does_not_expose_files_outside_bundled_assets(running, path):
    connection = http.client.HTTPConnection("127.0.0.1", running.server_port)
    connection.request("GET", path)
    assert connection.getresponse().status == HTTPStatus.NOT_FOUND


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


def test_catalog_bytes_follow_file_changes(project):
    server = _Server(0, project)
    try:
        first = server.catalog_bytes()
        assert server.catalog_bytes() is first
        (project / ".meow" / "workflows" / "fresh.toml").write_text(
            (builtin_dir() / "workflows" / "quick.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        assert b'"fresh"' in server.catalog_bytes()
    finally:
        server.server_close()


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


@pytest.mark.parametrize(
    ("kind", "source"),
    [("agents", "explorer"), ("workflows", "quick"), ("skills", "")],
)
def test_created_descriptions_round_trip_unicode_and_escapes(project, kind, source):
    description = 'Build \U0001f431 "carefully" with C:\\tools\nNext line.'
    if kind == "agents":
        description = description.replace("\n", " ")
    result = create_module.create_definition(
        project,
        kind,
        {"name": "roundtrip", "description": description, "source": source},
    )
    assert Path(result["file"]).is_file()
    item = next(
        item for item in build_catalog(project)[kind] if item["name"] == "roundtrip"
    )
    assert item["description"] == description


def test_remember_unicode_repository_preserves_existing_choices(project, tmp_path):
    other = tmp_path / "project-\U0001f431"
    other.mkdir()
    repos_module.remember(project)
    repos_module.remember(other)
    assert repos_module.repo_choices(project) == [
        str(project.resolve()),
        str(other.resolve()),
    ]


@pytest.mark.parametrize("kind", ["skills", "agents", "workflows"])
@pytest.mark.parametrize("scope", ["project", "user"])
def test_catalog_refreshes_table_form_custom_sources(project, tmp_path, kind, scope):
    folder = tmp_path / "definitions"
    folder.mkdir()
    if kind == "skills":
        target = folder / "fresh" / "SKILL.md"
        target.parent.mkdir()
        text = "---\nname: fresh\ndescription: Before.\n---\nBody.\n"
    else:
        target = folder / "fresh.toml"
        template = "explorer" if kind == "agents" else "quick"
        text = (builtin_dir() / kind / f"{template}.toml").read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    config = (
        project / ".meow" / "config.toml"
        if scope == "project"
        else create_module.user_config_path()
    )
    config.parent.mkdir(parents=True, exist_ok=True)
    relative = "definitions" if scope == "project" else folder.as_posix()
    config.write_text(
        f'[custom]\n{kind} = [{{path = "{relative}"}}]\n', encoding="utf-8"
    )
    with _Server(0, project) as server:
        before = server.catalog_bytes()
        updated = (
            text.replace("Before.", "After editing.")
            if kind == "skills"
            else text + "\n# Changed\n"
        )
        target.write_text(updated, encoding="utf-8")
        assert server.catalog_bytes() != before


def test_catalog_cache_does_not_scan_install_or_runtime_files(project, monkeypatch):
    user_dir = create_module.user_config_path().parent
    user_dir.mkdir(parents=True, exist_ok=True)
    runtime = user_dir / "versions" / "0.1.5" / "runtime"
    runtime.mkdir(parents=True)
    original = Path.rglob

    def definitions_only(path, pattern):
        assert path != user_dir, "Catalog refresh must not walk installed runtimes"
        return original(path, pattern)

    monkeypatch.setattr(Path, "rglob", definitions_only)
    with _Server(0, project) as server:
        assert b'"workflows"' in server.catalog_bytes()


def test_create_skill_lands_in_project_plugin_and_registers_it(running, project):
    status, body = _post(
        running.server_port,
        "/api/skills",
        {"name": "journal", "description": "Keeps a journal.", "source": ""},
    )

    assert status == HTTPStatus.CREATED
    plugin = project / ".meow" / "plugin"
    assert Path(body["file"]) == plugin / "skills" / "journal" / "SKILL.md"
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "meow-project"
    assert (
        json.loads((plugin.parent / ".claude-plugin" / "marketplace.json").read_text())[
            "name"
        ]
        == "meow-project"
    )
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert settings["extraKnownMarketplaces"]["meow-project"]["source"] == {
        "source": "directory",
        "path": "./.meow",
    }
    assert settings["enabledPlugins"]["meow-project@meow-project"] is True
    skills = {item["name"]: item for item in build_catalog(project)["skills"]}
    assert skills["journal"]["description"] == "Keeps a journal."


def test_create_skill_copies_existing_skill(running, project):
    status, _ = _post(
        running.server_port,
        "/api/skills",
        {"name": "notes-copy", "description": "Copy.", "source": "notes"},
    )

    assert status == HTTPStatus.CREATED
    copied = project / ".meow" / "plugin" / "skills" / "notes-copy" / "SKILL.md"
    assert copied.read_text(encoding="utf-8").startswith("---\nname: notes-copy\n")
    assert "Body." in copied.read_text(encoding="utf-8")


def test_create_skill_without_any_config_needs_no_custom_table(tmp_path):
    status, _ = _create(tmp_path, "skills", {"name": "fresh", "description": "New."})

    assert status == HTTPStatus.CREATED
    assert (tmp_path / ".meow" / "plugin" / "skills" / "fresh" / "SKILL.md").is_file()
    assert not (tmp_path / ".meow" / "config.toml").exists()
    assert "fresh" in {item["name"] for item in build_catalog(tmp_path)["skills"]}


def _create(project_dir, kind, payload):
    server = _Server(0, project_dir)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
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


def test_create_user_skill_registers_user_plugin(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".meow"
    monkeypatch.setattr(create_module, "user_config_path", lambda: home / "config.toml")
    fake_user = tmp_path / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user))
    project = tmp_path / "project"
    project.mkdir()

    status, _ = _create(
        project, "skills", {"name": "shared", "description": "Shared.", "scope": "user"}
    )

    assert status == HTTPStatus.CREATED
    assert (home / "plugin" / "skills" / "shared" / "SKILL.md").is_file()
    settings = json.loads((fake_user / ".claude" / "settings.json").read_text())
    assert settings["env"]["CLAUDE_CODE_PLUGIN_DIRS"] == str(home / "plugin")


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

    status, body = _post(
        port, "/api/agents", {"name": "fresh", "source": "explorer", "description": " "}
    )
    assert status == HTTPStatus.BAD_REQUEST
    assert "description" in body["error"]

    status, _ = _post(
        port,
        "/api/agents",
        {"name": "triage", "source": "explorer", "description": "d"},
    )
    assert status == HTTPStatus.CONFLICT

    status, _ = _post(
        port,
        "/api/agents",
        {"name": "fresh", "source": "missing", "description": "d"},
    )
    assert status == HTTPStatus.BAD_REQUEST

    status, _ = _post(
        port,
        "/api/agents",
        {"name": "fresh", "source": "explorer", "description": "d"},
        {"Content-Type": "text/plain"},
    )
    assert status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE

    status, _ = _post(
        port,
        "/api/agents",
        {"name": "fresh", "source": "explorer", "description": "d"},
        {"Origin": "http://evil.example"},
    )
    assert status == HTTPStatus.FORBIDDEN

    assert not (project / ".meow" / "agents" / "fresh.toml").exists()


def test_user_skill_loads_when_the_repo_is_the_home_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".meow" / "config.toml"
    monkeypatch.setattr(create_module, "user_config_path", lambda: config)
    monkeypatch.setattr("meow.project.config.user_config_path", lambda: config)

    status, _ = _create(
        home, "skills", {"name": "shared", "description": "Shared.", "scope": "user"}
    )

    assert status == HTTPStatus.CREATED
    catalog = build_catalog(home)
    assert catalog["error"] is None
    assert "shared" in {item["name"] for item in catalog["skills"]}


def test_page_load_rebuilds_catalog(running, monkeypatch):
    from meow.ui import catalog as catalog_module

    builds = []
    real_build = catalog_module.build_catalog
    monkeypatch.setattr(
        catalog_module,
        "build_catalog",
        lambda working_dir: builds.append(1) or real_build(working_dir),
    )
    base = f"http://127.0.0.1:{running.server_port}"

    urlopen(f"{base}/api/catalog").read()
    polled = len(builds)
    urlopen(f"{base}/api/catalog").read()
    assert len(builds) == polled

    urlopen(f"{base}/").read()
    urlopen(f"{base}/api/catalog").read()
    assert len(builds) == polled + 1


def test_project_skill_in_git_ignored_folder_is_refused(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".meow/\n", encoding="utf-8")

    status, body = _create(
        tmp_path,
        "skills",
        {"name": "ignored", "description": "No.", "scope": "project"},
    )

    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "ignored by git" in body["error"]
    assert not (tmp_path / ".meow" / "config.toml").exists()
    assert not (tmp_path / ".meow" / "skills" / "ignored").exists()

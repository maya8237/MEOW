import json
from pathlib import Path

from meow.project.command_detection import command_defaults
from meow.project.config import load_config

WEB_SERVER = (
    "export default { webServer: { command: 'bun run dev', "
    "url: 'http://localhost:5173'%s }, use: { channel: 'chrome' } };"
)


def infer(root, explicit=None):
    detected = command_defaults(root, explicit or {})
    return detected.defaults.get("tester", {}), detected


def vite_project(directory, manager_lock="bun.lock"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}})
    )
    (directory / manager_lock).write_text("")


def test_inner_bun_vite_project_gets_a_port_placeholder_server_in_its_cwd(tmp_path):
    vite_project(tmp_path / "frontend")
    tester, detected = infer(tmp_path)
    server = tester["dev_server"][0]
    assert server["cwd"] == "frontend"
    assert server["command"] == "bun run dev --port {port}"
    assert server["ready_url"] == "http://127.0.0.1:{port}/"
    assert "frontend/package.json" in detected.origins["tester.dev_server"]


def test_inner_playwright_config_gives_cwd_browser_and_composed_server_cwd(tmp_path):
    inner = tmp_path / "frontend"
    inner.mkdir()
    (inner / "playwright.config.ts").write_text(WEB_SERVER % "")
    tester, detected = infer(tmp_path)
    assert tester["dev_server"][0]["cwd"] == "frontend"
    assert tester["browser_channel"] == "chrome"
    assert "frontend/playwright.config.ts" in detected.origins["tester.browser_channel"]
    (inner / "playwright.config.ts").write_text(WEB_SERVER % ", cwd: 'app'")
    tester, _ = infer(tmp_path)
    assert tester["dev_server"][0]["cwd"] == "frontend/app"


def test_a_server_cwd_escaping_the_repo_is_dropped_with_a_note(tmp_path):
    inner = tmp_path / "frontend"
    inner.mkdir()
    (inner / "playwright.config.ts").write_text(WEB_SERVER % ", cwd: '../../x'")
    tester, detected = infer(tmp_path)
    assert "dev_server" not in tester
    assert any("webServer" in note for note in detected.ambiguous)


def test_root_setup_wins_over_an_inner_folder(tmp_path):
    vite_project(tmp_path)
    vite_project(tmp_path / "frontend")
    tester, _ = infer(tmp_path)
    assert tester["dev_server"][0]["cwd"] == "."


def test_conventional_folder_is_preferred_and_the_other_is_reported(tmp_path):
    vite_project(tmp_path / "admin")
    vite_project(tmp_path / "frontend")
    tester, detected = infer(tmp_path)
    assert tester["dev_server"][0]["cwd"] == "frontend"
    assert any("admin" in note for note in detected.ambiguous)


def test_dependency_hidden_and_symlinked_folders_are_ignored(tmp_path):
    vite_project(tmp_path / "node_modules" / "pkg")
    vite_project(tmp_path / ".cache")
    real = tmp_path.parent / (tmp_path.name + "-real")
    vite_project(real)
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    tester, _ = infer(tmp_path)
    assert tester == {}


def test_inner_base_url_only_config(tmp_path):
    inner = tmp_path / "web"
    inner.mkdir()
    (inner / "playwright.config.ts").write_text(
        "export default { use: { baseURL: 'http://localhost:4000' } }"
    )
    tester, _ = infer(tmp_path)
    assert tester["base_url"] == "http://localhost:4000"
    assert tester["browser_playwright"] is True


def test_load_config_end_to_end_for_a_bun_frontend_folder(tmp_path):
    vite_project(tmp_path / "frontend")
    (tmp_path / ".meow").mkdir()
    tester = load_config(Path(tmp_path))["tester"]
    assert tester["dev_server"][0].cwd == Path("frontend")
    assert tester["dev_server"][0].command.startswith("bun run dev")

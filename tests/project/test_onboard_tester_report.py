import subprocess

from meow.project.onboard_config import configure_project
from meow.project.onboarding import feature_gaps

WEB_SERVER = "export default { webServer: { command: 'npm run dev', port: 3000 } };"


def init_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)


def report(tmp_path, playwright=None, toml=""):
    init_repo(tmp_path)
    (tmp_path / ".meow").mkdir(exist_ok=True)
    if toml:
        (tmp_path / ".meow" / "config.toml").write_text(toml, encoding="utf-8")
    if playwright:
        (tmp_path / "playwright.config.ts").write_text(playwright, encoding="utf-8")
    return configure_project(tmp_path, [])


def test_report_lists_inferred_tester_values_with_sources(tmp_path):
    result = report(tmp_path, WEB_SERVER)
    source = "(inferred from playwright.config.ts)"
    assert f"Tester dev_server: npm run dev {source}" in result["inferred"]
    assert result["tester_ready"] is True


def test_report_says_what_could_not_be_read_and_is_not_ready(tmp_path):
    result = report(
        tmp_path, "export default { webServer: { command: process.env.C, port: 1 } }"
    )
    assert result["inferred"] == []
    assert result["tester_ready"] is False
    assert any("could not read" in note for note in result["could_not_read"])


def test_inferred_values_are_not_written_to_the_config(tmp_path):
    report(tmp_path, WEB_SERVER)
    written = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".meow").glob("config*.toml")
    )
    assert "dev_server" not in written
    assert "browser_playwright" not in written


def test_browser_launch_gap_tracks_any_of_the_three_selection_keys(tmp_path):
    init_repo(tmp_path)
    (tmp_path / ".meow").mkdir()
    assert feature_gaps(tmp_path)["tester_browser_launch"]["configured"] is False
    for toml in (
        '[tester]\nbrowser_channel = "chrome"\n',
        '[tester]\nbrowser_executable = "/usr/bin/chrome"\n',
        "[tester]\nbrowser_playwright = true\n",
    ):
        (tmp_path / ".meow" / "config.toml").write_text(toml, encoding="utf-8")
        assert feature_gaps(tmp_path)["tester_browser_launch"]["configured"] is True


def test_dev_server_gap_text_explains_the_port_placeholder():
    from meow.project.onboarding import _GAPS

    text = _GAPS["tester_dev_server"][1]
    assert "{port}" in text
    assert "--preflight" in text
    assert "--preflight" in _GAPS["tester_browser_launch"][1]

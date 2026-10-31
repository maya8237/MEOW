from pathlib import Path

from meow.project.custom.frontmatter import parse

ROOT = Path(__file__).resolve().parents[2]
MAX_LINES = 200


def text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_tester_doc_covers_setup_keys_ports_preflight_and_folders():
    body = text("docs/TESTER.md")
    for phrase in (
        "browser_channel",
        "browser_executable",
        "browser_playwright",
        "browser_env",
        "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS",
        "{port}",
        "meow native verify --preflight",
        "frontend/",
        "playwright.config",
        "excludedportrange",
    ):
        assert phrase in body, phrase


def test_docs_and_skills_stay_under_the_line_limit():
    for relative in (
        "docs/TESTER.md",
        "docs/INTEGRATIONS.md",
        "docs/ONBOARDING.md",
        "skills/test/SKILL.md",
        "skills/onboard/SKILL.md",
    ):
        assert len(text(relative).splitlines()) < MAX_LINES, relative


def test_integrations_and_agents_point_to_the_tester_doc():
    assert "TESTER.md" in text("docs/INTEGRATIONS.md")
    assert "docs/TESTER.md" in text("AGENTS.md")


def test_test_skill_has_the_shared_setup_loop():
    metadata, body = parse(ROOT / "skills" / "test" / "SKILL.md")
    assert metadata["name"] == "test"
    for phrase in (
        "## Tester setup",
        "--preflight",
        "explicit yes",
        "3 iterations",
        "snippet",
        "never a port number",
        ".meow/config.local.toml",
    ):
        assert phrase in body, phrase
    assert body.index("## Tester setup") < body.index("meow run")


def test_onboard_skill_uses_the_shared_loop():
    body = text("skills/onboard/SKILL.md")
    assert "../test/SKILL.md#tester-setup" in body
    assert "--preflight" in body

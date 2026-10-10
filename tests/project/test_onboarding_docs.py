"""Docs describe automatic onboarding and `/onboard` as the add-ons tool."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_onboard_skill_checks_state_and_focuses_on_add_ons():
    text = _read("skills", "onboard", "SKILL.md")
    assert "meow native onboard-status" in text
    assert "add-ons" in text
    assert "never been onboarded" in text


def test_readme_says_first_run_onboards_automatically():
    text = _read("README.md")
    assert "Set up once" not in text
    assert "first `meow run`" in text
    assert "add-ons" in text


def test_cli_guide_documents_automatic_onboarding():
    text = _read("docs", "CLI.md")
    assert "onboards" in text
    assert "first run" in text


def test_agents_and_integrations_point_onboard_at_add_ons():
    assert "add-ons" in _read("AGENTS.md")
    assert "add-ons" in _read("docs", "INTEGRATIONS.md")


def test_shared_protocol_says_run_sets_up_missing_config():
    text = _read("skills", "_shared", "native-mode.md")
    assert "onboards the project" in text

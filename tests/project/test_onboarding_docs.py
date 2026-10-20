"""Docs describe automatic onboarding and `/onboard` as the add-ons tool."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_cli_doc_covers_orchestrate():
    text = _read("docs", "CLI.md")
    for needle in (
        "meow run --orchestrate",
        "--jql",
        "--jira",
        "--tasks",
        "--max-parallel",
        "--no-pr",
        "--dry-run",
        "meow resume ID --continue",
        "Exit code 3",
        "PR not opened",
        "`-o`",
        "/meow:run",
    ):
        assert needle in text, needle


def test_cli_doc_keeps_the_orchestrator_worker_internal():
    text = _read("docs", "CLI.md")
    assert text.index("## Maintainer and internal commands") < text.index(
        "`meow _orchestrator-task`"
    )
    assert text.index("## Multi-sprint runs") < text.index("## Review")


def test_integrations_doc_covers_the_orchestrator_table():
    text = _read("docs", "INTEGRATIONS.md")
    for needle in (
        "[orchestrator]",
        "max_parallel = 3",
        "max_recovery_cycles = 3",
        "review_rounds = 3",
        'branch_prefix = "orchestrator/"',
        "poll_seconds = 5",
        "open_pr = true",
    ):
        assert needle in text, needle


def test_integrations_doc_explains_pull_request_creation():
    text = _read("docs", "INTEGRATIONS.md")
    for needle in (
        "GITLAB_URL",
        "create_pull_request",
        "create_merge_request",
        "provider not configured",
        "PR not opened",
    ):
        assert needle in text, needle


def test_architecture_doc_names_the_orchestrator_packages():
    text = _read("docs", "ARCHITECTURE.md")
    assert "execution/orchestration/" in text
    assert "agents/orchestration.py" in text
    assert "native_orchestrate.py" in text


def test_agents_file_lists_the_orchestrate_skill_and_package():
    text = _read("AGENTS.md")
    assert "Run defaults to CLI" in text
    assert "orchestration/" in text

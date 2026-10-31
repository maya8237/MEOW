import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.agents.tester import VerificationAgent, browser_launch_options
from meow.infrastructure.test_runner import VerificationStageEvidence
from meow.project import prompts
from meow.project.plan_files import stage_file

ENV = {"PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS": "true"}


class Context:
    def __init__(self, root, **tester):
        self.repo_dir = root
        self.config = {
            "models": {"tester": "haiku"},
            "docs_dir": "docs",
            "lint_timeout": 60,
            "tester": {
                "tests": [],
                "dev_server": [],
                "mcp": [],
                "test_dirs": [],
                "base_url": None,
                "architecture_files": [],
                "browser_channel": None,
                "browser_executable": None,
                "browser_playwright": False,
                "browser_env": {},
                **tester,
            },
        }

    def model(self, role, agent=None):
        return self.config["models"].get(role)

    def active_working_dir(self):
        return self.repo_dir


def render_prompt(root, **tester):
    """The prompt the tester receives for a request, given [tester] settings."""
    report = stage_file(root / "docs", "tester")
    report.parent.mkdir(parents=True, exist_ok=True)

    def write_report(prompt, options, role):
        report.write_text(
            "SUMMARY: ok\nSTATUS: PASS\n\n## Test plan\n- x", encoding="utf-8"
        )

    with patch.object(
        VerificationAgent, "run_query", new=AsyncMock(side_effect=write_report)
    ) as run:
        asyncio.run(
            VerificationAgent(Context(root, **tester)).test_request(
                "Audit page", VerificationStageEvidence()
            )
        )
    return run.await_args.args[0]


@pytest.mark.parametrize(
    ("tester", "expected"),
    [
        ({"browser_channel": "chrome"}, {"channel": "chrome"}),
        (
            {"browser_executable": "/usr/bin/chrome"},
            {"executablePath": "/usr/bin/chrome"},
        ),
        ({"browser_playwright": True}, {}),
        ({}, None),
    ],
)
def test_launch_options_per_selection(tester, expected):
    assert browser_launch_options(tester) == expected


def test_prompt_has_launch_options_and_env_lines(tmp_path):
    prompt = render_prompt(
        tmp_path,
        base_url="http://127.0.0.1:3000",
        browser_channel="chrome",
        browser_env=ENV,
    )
    assert f"Browser launch options: {json.dumps({'channel': 'chrome'})}" in prompt
    assert (
        "Environment for Playwright: PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true"
        in prompt
    )


def test_prompt_says_not_configured_when_a_frontend_has_no_selection(tmp_path):
    prompt = render_prompt(tmp_path, base_url="http://127.0.0.1:3000")
    assert "Browser launch options: NOT CONFIGURED" in prompt


def test_prompt_is_quiet_without_a_frontend_or_selection(tmp_path):
    prompt = render_prompt(tmp_path)
    assert "NOT CONFIGURED" not in prompt
    assert "Environment for Playwright: (none)" in prompt


def test_tester_prompt_forbids_other_browsers_and_fails_when_unconfigured():
    text = prompts.tester_prompt(Path("r.md"))
    for phrase in (
        "never try another browser",
        "browser not configured",
        "browser_channel",
        "browser_executable",
        "browser_playwright",
        "LAUNCH",
    ):
        assert phrase in text, phrase

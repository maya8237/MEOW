import subprocess
import sys
from pathlib import Path

import pytest

from meow.cli.parser import _build_arg_parser
from meow.infrastructure import verification_policy as policy
from meow.infrastructure.checks import Check, configured_checks, run_final_checks
from meow.project.config import load_config
from meow.project.config_models import VerificationCommand


@pytest.mark.parametrize("command", ["run", "review", "_orchestrator-task"])
def test_force_disable_flag_is_available(command):
    argv = [command, "x", "y"] if command.startswith("_") else [command, "x"]
    args = _build_arg_parser().parse_args([*argv, "--force-disable-tests"])
    assert args.force_disable_tests


def test_disabled_test_commands_and_browser_never_run(tmp_path):
    import asyncio

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text("", encoding="utf-8")
    config = load_config(tmp_path)
    config["tester"]["tests"] = [
        VerificationCommand(Path("."), sys.executable, ("-c", "raise SystemExit(7)"))
    ]
    config["tester"]["browser"] = {"entrypoint": "missing-browser", "required": True}
    config["build"] = [Check("build", "build", sys.executable, ("-c", "pass"))]
    skipped = policy.disable_tests(config)
    results = asyncio.run(run_final_checks(tmp_path, config))
    assert skipped == ["AI tester", "test-1", "browser"]
    assert [check.kind for check in configured_checks(config)] == ["build"]
    assert len(results) == 1 and results[0].passed


@pytest.mark.parametrize(
    "path, expected",
    [
        ("README.md", False),
        ("docs/guide.md", False),
        ("src/code.py", True),
        ("skills/run/SKILL.md", True),
        ("docs/example.py", True),
        ("requirements.txt", True),
        ("AGENTS.md", True),
        ("tests/test_api.py", True),
    ],
)
def test_auto_tester_risk_uses_changed_content_paths(tmp_path, path, expected):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed", encoding="utf-8")
    assert policy.behavior_changed(tmp_path) is expected


def test_unknown_or_empty_diff_keeps_auto_tester_enabled(tmp_path):
    assert policy.behavior_changed(tmp_path)

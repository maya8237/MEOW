"""Public provider commands bind before their first configuration load."""

import json
import subprocess
from unittest.mock import AsyncMock

import pytest

from meow.cli.cli import cli_main
from meow.project.config import load_config


@pytest.fixture
def registered_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "company-jira": {"command": "python", "args": ["-m", "jira_mcp"]},
                "company-gitlab": {"command": "python", "args": ["-m", "gitlab_mcp"]},
            }
        })
    )
    subprocess.run(["git", "add", ".mcp.json"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@e.c",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_first_jira_command_loads_discovered_binding(registered_repo, monkeypatch):
    solve = AsyncMock(return_value={"issue": "APP-1", "branch": "issue/APP-1"})
    monkeypatch.setattr("meow.integrations.issue_solver._solve_issue", solve)
    cli_main(["run", "--jira", "APP-1", "--work-dir", str(registered_repo)])
    config, jira = solve.await_args.args[1:3]
    assert config["jira"]["mcp"] == {"from": "company-jira"}
    assert jira["mcp"]["command"] == "python"
    assert not (registered_repo / ".meow/config.toml").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", ".meow"],
        cwd=registered_repo,
        capture_output=True,
        check=True,
    )
    assert status.stdout == b""


def test_first_gitlab_review_loads_discovered_binding(registered_repo, monkeypatch):
    review = AsyncMock()
    monkeypatch.setattr("meow.cli.review_cli._gitlab_review", review)
    cli_main([
        "review",
        "--gitlab",
        "https://gitlab.example/a/b/-/merge_requests/1",
        "--work-dir",
        str(registered_repo),
    ])
    assert review.await_args.args[1]["gitlab"]["mcp"] == {"from": "company-gitlab"}


def test_orchestration_intake_binds_before_provider_validation(registered_repo):
    from meow.cli.cli import _build_arg_parser
    from meow.cli.orchestration_cli import _refusal

    args = _build_arg_parser().parse_args(["run", "--jira", "APP-1", "APP-2"])
    assert _refusal(args, registered_repo, None) is None
    assert load_config(registered_repo)["jira"]["mcp"] == {"from": "company-jira"}
    assert not (registered_repo / ".meow/config.toml").exists()


def test_dirty_orchestration_refuses_before_detection(registered_repo, monkeypatch):
    from meow.cli.cli import _build_arg_parser
    from meow.cli.orchestration_cli import EXIT_FAILED, _refusal

    (registered_repo / "uncommitted.py").write_text("dirty")
    monkeypatch.setattr(
        "meow.project.mcp_registry.discover_provider_references",
        lambda *a: pytest.fail("detection before dirty guard"),
    )
    args = _build_arg_parser().parse_args(["run", "--jira", "APP-1", "APP-2"])
    assert _refusal(args, registered_repo, None) == EXIT_FAILED
    assert not (registered_repo / ".meow").exists()

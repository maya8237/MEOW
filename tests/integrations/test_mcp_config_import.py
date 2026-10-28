import json
import subprocess

import pytest

from meow.cli.cli import cli_main
from meow.project.config import load_config
from meow.project.onboarding import onboard_project


@pytest.mark.parametrize(
    "case",
    [
        (
            "",
            'command = "explicit"\nargs = ["server"]\n',
            [],
            {"command": "explicit", "args": ["server"]},
        ),
        (
            'command = "old"\nargs = ["old"]\n',
            'from = "chosen"\n',
            [],
            {"from": "chosen"},
        ),
        ('from = "old-binding"\n', "", [], {}),
        (
            "",
            'from = "chosen"\n',
            ["--jira-command", "flag-command"],
            {"command": "flag-command"},
        ),
    ],
)
def test_config_import_replaces_connection_and_preserves_flag_precedence(
    tmp_path, case
):
    existing, imported, flags, expected = case
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert onboard_project(tmp_path).error is None
    local = tmp_path / ".meow/config.local.toml"
    local.write_text(
        '[jira]\nbranch_prefix = "keep/"\n'
        + ("[jira.mcp]\n" + existing if existing else ""),
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "jira": {"command": "jira-mcp"},
                "gitlab": {"command": "gitlab-mcp"},
            }
        }),
        encoding="utf-8",
    )
    config_file = tmp_path / "import.toml"
    config_file.write_text(
        '[delivery]\ntarget_branch = "file-target"\n'
        '[jira]\nproject_key = "FILE"\n[jira.mcp]\n' + imported,
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as result:
        cli_main([
            "onboard",
            "--non-interactive",
            "--work-dir",
            str(tmp_path),
            "--config",
            str(config_file),
            "--target",
            "flag-target",
            *flags,
        ])
    assert result.value.code == 0
    config = load_config(tmp_path)
    assert config["jira"]["mcp"] == expected
    assert config["jira"]["project_key"] == "FILE"
    assert config["jira"]["branch_prefix"] == "keep/"
    assert config["gitlab"]["mcp"] == {"from": "gitlab"}
    assert config["delivery"]["target_branch"] == "flag-target"

import json

import pytest

from meow.cli import cli as cli_module
from meow.cli.onboard_cli import onboard_command
from meow.project.custom import kit_ops

from .conftest import SKILL

HOOK = 'description = "g"\nevent = "PreToolUse"\nmatcher = "Write"\ncommand = "{c}"\n'


@pytest.fixture
def kit(make_kit):
    return make_kit({
        "skills/s/SKILL.md": SKILL.format(name="s", body="x"),
        "hooks/g.toml": HOOK.format(c="echo guard"),
    })


def onboard(project, capsys, *argv):
    args = cli_module._build_arg_parser().parse_args([
        "onboard", "--non-interactive", "--work-dir", str(project), *argv,
    ])  # fmt: skip
    status = onboard_command(args, project)
    return status, json.loads(capsys.readouterr().out)  # stdout is only JSON


def test_kit_flag_installs_a_project_kit_and_lists_what_it_runs(
    kit, project, user_dir, capsys
):
    status, result = onboard(project, capsys, "--kit", str(kit.path))
    assert status == 0
    [item] = result["kits"]
    assert item["status"] == "installed" and item["scope"] == "project"
    assert "hook runs: g (PreToolUse): echo guard" in item["contents"]
    spec = kit_ops.collect_specs(project, user_dir)[0]
    assert spec.scope == "project" and spec.rev  # pinned in the committed config
    assert (project / ".claude/skills/s/SKILL.md").is_file()  # published
    assert "kits_synced" in result


def test_repeating_the_command_changes_nothing(kit, project, user_dir, capsys):
    onboard(project, capsys, "--kit", str(kit.path))
    config = (project / ".meow/config.toml").read_text()
    status, result = onboard(project, capsys, "--kit", str(kit.path))
    assert status == 0 and result["kits"][0]["status"] == "unchanged"
    assert (project / ".meow/config.toml").read_text() == config


def test_a_bad_kit_fails_the_command_but_not_the_good_one(
    kit, project, user_dir, capsys
):
    status, result = onboard(project, capsys, "--kit", "nope", "--kit", str(kit.path))
    assert status == 1
    assert [item["status"] for item in result["kits"]] == ["failed", "installed"]
    assert "not a kit location" in result["kits"][0]["detail"]
    assert len(kit_ops.collect_specs(project, user_dir)) == 1


def test_kit_scope_local_stays_out_of_the_shared_config(kit, project, user_dir, capsys):
    status, result = onboard(
        project, capsys, "--kit", str(kit.path), "--kit-scope", "local"
    )
    assert status == 0 and result["kits"][0]["scope"] == "local"
    assert "kits" not in (project / ".meow/config.toml").read_text()
    assert "[[kits]]" in (project / ".meow/config.local.toml").read_text()


def test_no_kit_flag_leaves_the_result_unchanged(project, user_dir, capsys):
    status, result = onboard(project, capsys)
    assert status == 0 and "kits" not in result and "kits_synced" not in result

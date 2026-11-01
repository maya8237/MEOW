"""Registering hook definitions as a Claude Code plugin."""

import json
import math
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.custom.hook_sync import check_hook_registration, sync_hook_plugins

HOOK = """\
description = "Guard edits"
event = "PreToolUse"
matcher = "Write|Edit"
command = "true"
timeout = 12.5
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    (user_home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    return user_home


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / ".meow").mkdir(parents=True)
    return root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sync(project, home, scopes=("local", "project", "user")):
    hooks = load_config(project)["customizations"].hooks
    return sync_hook_plugins(hooks, project, home / ".meow", scopes)


def check(project, home):
    hooks = load_config(project)["customizations"].hooks
    return check_hook_registration(hooks, project, home / ".meow")


def test_sync_writes_hooks_json_and_registers_the_plugin(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)

    assert check(project, home)
    sync(project, home)

    config = json.loads((project / ".meow/plugin/hooks/hooks.json").read_text())
    (entry,) = config["hooks"]["PreToolUse"]
    command = entry["hooks"][0]
    assert entry["matcher"] == "^(?:Write|Edit)$"
    assert "MEOW_HOOK=guard MEOW_EVENT=PreToolUse" in command["command"]
    assert "meow native" not in command["command"]
    assert command["timeout"] == math.ceil(12.5)
    assert "meow-project" in (project / ".claude/settings.json").read_text()
    assert check(project, home) == []
    assert sync(project, home) == []


def test_role_limited_and_lifecycle_hooks_stay_with_meow(project, home):
    write(project / ".meow/hooks/a.toml", HOOK + 'roles = ["generator"]\n')
    write(
        project / ".meow/hooks/b.toml",
        'description = "x"\nevent = "sprint_end"\ncommand = "true"\n',
    )

    assert sync(project, home) == []
    assert not (project / ".meow/plugin/hooks/hooks.json").exists()


def test_removed_definition_removes_hooks_json(project, home):
    hook = project / ".meow/hooks/guard.toml"
    write(hook, HOOK)
    sync(project, home)
    hook.unlink()

    assert any("removed" in line for line in sync(project, home))
    assert not (project / ".meow/plugin/hooks/hooks.json").exists()


def test_only_the_winning_scope_is_synced(project, home):
    write(project / ".meow/hooks/guard.toml", HOOK)
    write(project / ".meow/local/hooks/guard.toml", HOOK.replace("true", "false"))

    sync(project, home)

    assert (project / ".meow/local/plugin/hooks/hooks.json").is_file()
    assert not (project / ".meow/plugin/hooks/hooks.json").exists()


def test_hooks_and_sync_commands(project, home, capsys):
    from meow.cli.hooks_cli import hooks_command
    from meow.cli.parser import _build_arg_parser
    from meow.cli.sync_cli import sync_command

    write(project / ".meow/hooks/guard.toml", HOOK)
    write(
        project / ".meow/local/plugin/skills/mine/SKILL.md",
        "---\nname: mine\ndescription: Does mine.\n---\n\nBody.\n",
    )
    parse = _build_arg_parser().parse_args

    with pytest.raises(ValueError, match="meow hooks sync"):
        hooks_command(parse(["hooks", "sync", "--check"]), project)
    with pytest.raises(ValueError, match="meow sync"):
        sync_command(parse(["sync", "--check"]), project)

    sync_command(parse(["sync"]), project)
    assert "restart Claude Code" in capsys.readouterr().out
    assert (project / ".meow/plugin/hooks/hooks.json").is_file()
    assert (project / ".meow/local/plugin/skills/mine").is_dir()

    hooks_command(parse(["hooks", "sync", "--check"]), project)
    sync_command(parse(["sync", "--check"]), project)
    assert "are registered" in capsys.readouterr().out


def test_hand_written_entries_are_kept_beside_generated_ones(project, home):
    own = project / ".meow/plugin/hooks/hooks.json"
    mine = {"hooks": [{"type": "command", "command": "echo mine"}]}
    write(own, json.dumps({"description": "Mine", "hooks": {"Stop": [mine]}}))

    assert sync(project, home) == []

    write(project / ".meow/hooks/guard.toml", HOOK)
    sync(project, home)
    config = json.loads(own.read_text())
    assert config["description"] == "Mine"
    assert config["hooks"]["Stop"] == [mine]
    assert "MEOW_HOOK=guard" in json.dumps(config["hooks"]["PreToolUse"])

    (project / ".meow/hooks/guard.toml").unlink()
    sync(project, home)
    assert json.loads(own.read_text())["hooks"] == {"Stop": [mine]}


def test_entries_from_earlier_versions_are_replaced(project, home):
    own = project / ".meow/plugin/hooks/hooks.json"
    old = {"hooks": [{"type": "command", "command": "meow native hook Stop --name x"}]}
    write(own, json.dumps({"hooks": {"Stop": [old]}}))
    write(project / ".meow/hooks/guard.toml", HOOK)

    sync(project, home)

    assert "Stop" not in json.loads(own.read_text())["hooks"]


def test_malformed_hooks_json_is_reported_not_overwritten(project, home):
    own = project / ".meow/plugin/hooks/hooks.json"
    write(own, "{nope")
    write(project / ".meow/hooks/guard.toml", HOOK)

    assert any("not valid JSON" in problem for problem in check(project, home))
    with pytest.raises(ValueError, match="not valid JSON"):
        sync(project, home)
    assert own.read_text() == "{nope"


def run_generated(project, command: str, env: dict | None = None):
    """Run a generated hooks.json command the way Claude Code would."""
    import subprocess

    write(
        project / ".meow/hooks/guard.toml", HOOK.replace('"true"', json.dumps(command))
    )
    hooks = load_config(project)["customizations"].hooks
    (hook,) = [h for h in hooks if h.name == "guard"]
    from meow.project.custom.hook_sync import hook_command

    return subprocess.run(
        ["sh", "-c", hook_command(hook)],
        input='{"tool_name": "Write"}',
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PROJECT_DIR": str(project),
            **(env or {}),
        },
        check=False,
    )


def test_generated_command_denies_with_exit_2_and_feedback(project, home):
    result = run_generated(project, "cat >/dev/null; echo no >&2; exit 2")
    assert (result.returncode, result.stderr.strip()) == (2, "no")


def test_generated_command_can_only_inform_never_grant(project, home):
    allow = '{"hookSpecificOutput": {"permissionDecision": "allow"}}'
    result = run_generated(project, f"echo '{allow}'; exit 1")
    assert (result.returncode, result.stdout) == (0, "")


def test_generated_command_sees_payload_env_and_project_dir(project, home):
    result = run_generated(
        project,
        'grep -q Write && [ "$MEOW_HOOK" = guard ] && [ "$(pwd -P)" = "$EXPECT" ] '
        "|| exit 2",
        {"EXPECT": str(project.resolve())},
    )
    assert result.returncode == 0, result.stderr


def test_generated_command_is_skipped_inside_meow_agents(project, home):
    result = run_generated(project, "exit 2", {"MEOW_SDK_AGENT": "1"})
    assert result.returncode == 0


def test_sdk_agent_variable_matches_the_runtime():
    from meow.infrastructure.user_hooks import SDK_AGENT_ENV
    from meow.project.custom import hook_sync

    assert hook_sync.SDK_AGENT_ENV == SDK_AGENT_ENV

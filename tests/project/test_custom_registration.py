"""Keeping every customization kind working at every scope, automatically."""

import json
import subprocess
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.custom.registration import (
    auto_sync_message,
    check_registration,
    hook_output,
    sync_registration,
)
from meow.project.custom.sources import unshared_project_dirs
from meow.project.onboarding import BOUNDARY

ROOT = Path(__file__).resolve().parents[2]
HOOK = 'description = "Guard"\nevent = "PreToolUse"\ncommand = "true"\n'


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
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("\n".join(BOUNDARY) + "\n", encoding="utf-8")
    return root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def skill(folder: Path, name: str) -> None:
    write(
        folder / name / "SKILL.md",
        f"---\nname: {name}\ndescription: Does {name}.\n---\n\nBody.\n",
    )


def every_scope(project: Path, home: Path) -> None:
    """A skill and a hook at local, project, and user scope, plus a linked skill."""
    bases = {
        "local": project / ".meow" / "local",
        "project": project / ".meow",
        "user": home / ".meow",
    }
    for scope, base in bases.items():
        skill(base / "plugin" / "skills", f"s-{scope}")
        write(base / "hooks" / f"guard-{scope}.toml", HOOK.replace("Guard", scope))
    skill(project / "tools" / "skills", "linked-one")
    write(project / ".meow" / "config.toml", '[custom]\nskills = ["tools/skills"]\n')


def custom(project: Path):
    return load_config(project)["customizations"]


def settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_auto_sync_fixes_every_scope(project, home):
    every_scope(project, home)

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "Restart Claude Code" in message
    assert "Commit the .meow/" in message
    assert "need attention" not in message
    shared = settings(project / ".claude" / "settings.json")["enabledPlugins"]
    assert shared == {"meow-project@meow-project": True}
    local = settings(project / ".claude" / "settings.local.json")["enabledPlugins"]
    assert local == {
        "meow-local@meow-local": True,
        "meow-project-linked@meow-project-linked": True,
    }
    user = settings(home / ".claude" / "settings.json")["env"]
    assert str((home / ".meow" / "plugin").resolve()) in user["CLAUDE_CODE_PLUGIN_DIRS"]
    for base in (project / ".meow" / "local", project / ".meow", home / ".meow"):
        assert (base / "plugin" / "hooks" / "hooks.json").is_file()
    linked = project / ".meow" / "linked" / "plugin" / "skills" / "linked-one"
    assert (linked / "SKILL.md").is_file()
    assert check_registration(custom(project), project, home / ".meow") == []
    assert auto_sync_message(custom(project), project, home / ".meow") is None


def test_auto_sync_picks_up_later_edits(project, home):
    every_scope(project, home)
    auto_sync_message(custom(project), project, home / ".meow")

    write(
        project / ".meow" / "hooks" / "guard-project.toml",
        HOOK + 'matcher = "Bash"\n',
    )
    skill(project / "tools" / "skills", "linked-two")

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "out of date" in message
    hooks = (project / ".meow" / "plugin" / "hooks" / "hooks.json").read_text()
    assert '"matcher": "^(?:Bash)$"' in hooks
    assert (project / ".meow" / "linked" / "plugin" / "skills" / "linked-two").is_dir()


def test_sync_shares_a_project_plugin_enabled_only_locally(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    write(
        project / ".claude" / "settings.local.json",
        json.dumps({
            "extraKnownMarketplaces": {
                "meow-project": {"source": {"source": "directory", "path": "./.meow"}}
            },
            "enabledPlugins": {"meow-project@meow-project": True},
        }),
    )

    assert sync_registration(custom(project), project, home / ".meow")

    shared = settings(project / ".claude" / "settings.json")
    assert shared["enabledPlugins"] == {"meow-project@meow-project": True}
    assert sync_registration(custom(project), project, home / ".meow") == []


def test_auto_sync_restores_the_gitignore_boundary(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    write(project / ".meow" / "config.toml", "")
    old = [rule for rule in BOUNDARY if rule != "!.meow/hooks/"]
    (project / ".gitignore").write_text("\n".join(old) + "\n", encoding="utf-8")
    assert unshared_project_dirs(project)

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and ".gitignore: restored" in message
    assert "need attention" not in message
    assert unshared_project_dirs(project) == []


def test_one_broken_scope_does_not_stop_the_others(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    skill(home / ".meow" / "plugin" / "skills", "mine")
    write(project / ".claude" / "settings.json", "{not json")

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "not valid JSON" in message
    assert (home / ".claude" / "settings.json").is_file()


def test_ignored_project_definitions_are_reported(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    write(project / ".meow" / "agents" / "helper.toml", "")
    (project / ".gitignore").write_text(".meow/hooks/\n", encoding="utf-8")

    problems = unshared_project_dirs(project)

    assert len(problems) == 1
    assert "project hooks" in problems[0] and "meow onboard" in problems[0]


def test_auto_sync_reports_what_it_cannot_fix(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    (project / ".gitignore").write_text(".meow/\n", encoding="utf-8")

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "need attention" in message
    assert "Restart" not in message
    assert not (project / ".claude").exists()


def test_sync_auto_command_prints_hook_json_and_never_fails(project, home, capsys):
    from meow.cli.parser import _build_arg_parser
    from meow.cli.sync_cli import sync_command

    args = _build_arg_parser().parse_args(["sync", "--auto"])
    skill(project / ".meow" / "plugin" / "skills", "team")
    sync_command(args, project)
    output = json.loads(capsys.readouterr().out)
    assert "Restart" in output["systemMessage"]
    assert output["hookSpecificOutput"] == {
        "hookEventName": "SessionStart",
        "reloadSkills": True,
    }

    sync_command(args, project)
    assert capsys.readouterr().out == ""

    write(project / ".meow" / "config.toml", "not = [valid")
    sync_command(args, project)
    assert "could not sync" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_hook_output_is_empty_without_a_message():
    assert hook_output(None) == ""


def test_post_tool_use_output_has_no_session_start_fields():
    assert json.loads(hook_output("x", "PostToolUse")) == {"systemMessage": "x"}


def test_plugin_runs_auto_sync_when_claude_code_starts():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = config["hooks"]["SessionStart"]
    command = entry["hooks"][0]["command"]
    assert entry["matcher"] == "startup"
    assert "meow sync --auto" in command
    assert command.startswith("command -v meow") and command.endswith("|| true")


def plugin_edit_hook(stdin: str, tmp_path: Path) -> str:
    """Run the plugin's PostToolUse command with a fake `meow` that logs its args."""
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = config["hooks"]["PostToolUse"]
    assert entry["matcher"] == "^(?:Write|Edit|MultiEdit)$"
    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls"
    write(bin_dir / "meow", f'#!/bin/sh\necho "$@" >> {log}\n')
    (bin_dir / "meow").chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", entry["hooks"][0]["command"]],
        input=stdin,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    return log.read_text() if log.exists() else ""


def test_plugin_syncs_right_after_a_customization_is_edited(tmp_path):
    edit = '{"tool_input": {"file_path": "/repo/.meow/hooks/guard.toml"}}'
    assert "sync --auto PostToolUse" in plugin_edit_hook(edit, tmp_path)


def test_plugin_ignores_unrelated_edits(tmp_path):
    edit = '{"tool_input": {"file_path": "/repo/src/app.py"}}'
    assert plugin_edit_hook(edit, tmp_path) == ""


def test_auto_sync_serves_hooks_from_configured_directories(project, home):
    write(project / "tools" / "hooks" / "cfg.toml", HOOK + 'matcher = "Bash"\n')
    write(project / ".meow" / "config.toml", '[custom]\nhooks = ["tools/hooks"]\n')

    auto_sync_message(custom(project), project, home / ".meow")

    hooks = json.loads(
        (project / ".meow" / "plugin" / "hooks" / "hooks.json").read_text()
    )
    (entry,) = hooks["hooks"]["PreToolUse"]
    assert entry["matcher"] == "^(?:Bash)$"
    assert "MEOW_HOOK=cfg" in entry["hooks"][0]["command"]
    assert check_registration(custom(project), project, home / ".meow") == []

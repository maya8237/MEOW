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
    assert entry["matcher"] == "Write|Edit"
    assert command["command"].startswith("meow native hook PreToolUse --name guard")
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

"""MEOW never treats the home directory or a non-git folder as a project."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from meow.cli.sync_cli import sync_command
from meow.project.custom.registration import repair_gitignore


def _args(**kwargs):
    return SimpleNamespace(auto=None, check=False, scope="all", **kwargs)


def test_sync_from_home_writes_no_gitignore_or_project_scope():
    home = Path.home()
    (home / ".meow").mkdir()
    (home / ".meow/config.toml").write_text("", encoding="utf-8")
    sync_command(_args(), home)
    assert not (home / ".gitignore").exists()
    assert repair_gitignore(home) == []


def test_non_git_folder_gets_no_gitignore(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text("", encoding="utf-8")
    assert repair_gitignore(tmp_path) == []
    assert not (tmp_path / ".gitignore").exists()


def test_sync_fails_when_a_skill_name_is_taken(tmp_path, capsys):
    skills = Path.home() / ".claude/skills/mine"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("x", encoding="utf-8")
    src = Path.home() / ".meow/plugin/skills/mine"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unresolved"):
        sync_command(_args(), tmp_path)
    assert "did not publish" in capsys.readouterr().err

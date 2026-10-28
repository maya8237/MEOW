"""Registering custom skills with Claude Code at every scope."""

import json
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.custom.sync import check_skill_registration, sync_skill_plugins


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


def skill(directory: Path, name: str) -> None:
    folder = directory / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Does {name}.\n---\n\nBody.\n",
        encoding="utf-8",
    )


def sync(project, home, scopes=("local", "project", "user")):
    skills = load_config(project)["customizations"].skills
    return sync_skill_plugins(skills, project, home / ".meow", scopes)


def check(project, home):
    skills = load_config(project)["customizations"].skills
    return check_skill_registration(skills, project, home / ".meow")


def settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_hand_made_skill_in_every_scope_is_registered(project, home):
    skill(project / ".meow" / "local" / "plugin" / "skills", "mine")
    skill(project / ".meow" / "plugin" / "skills", "team")
    skill(home / ".meow" / "plugin" / "skills", "everywhere")

    assert sync(project, home)

    local = settings(project / ".claude" / "settings.local.json")
    shared = settings(project / ".claude" / "settings.json")
    user = settings(home / ".claude" / "settings.json")
    assert local["enabledPlugins"] == {"meow-local@meow-local": True}
    assert local["extraKnownMarketplaces"]["meow-local"]["source"] == {
        "source": "directory",
        "path": "./.meow/local",
    }
    assert shared["enabledPlugins"] == {"meow-project@meow-project": True}
    assert user["env"]["CLAUDE_CODE_PLUGIN_DIRS"] == str(
        (home / ".meow" / "plugin").resolve()
    )
    manifest = project / ".meow" / "plugin" / ".claude-plugin" / "plugin.json"
    assert json.loads(manifest.read_text())["name"] == "meow-project"
    assert check(project, home) == []


def test_sync_is_idempotent_and_keeps_existing_settings(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    path = project / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"enabledPlugins": {"other@other": True}, "model": "x"}),
        encoding="utf-8",
    )

    assert sync(project, home)
    first = path.read_text(encoding="utf-8")

    assert sync(project, home) == []
    assert path.read_text(encoding="utf-8") == first
    data = settings(path)
    assert data["model"] == "x"
    assert data["enabledPlugins"]["other@other"] is True
    assert data["enabledPlugins"]["meow-project@meow-project"] is True


def test_scopes_without_skills_are_left_alone(project, home):
    assert sync(project, home) == []
    assert not (project / ".claude").exists()
    assert not (home / ".claude").exists()


def test_scope_option_limits_what_is_registered(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    skill(home / ".meow" / "plugin" / "skills", "everywhere")

    sync(project, home, ("user",))

    assert (home / ".claude" / "settings.json").is_file()
    assert not (project / ".claude").exists()


def test_malformed_settings_are_not_overwritten(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    path = project / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        sync(project, home)

    assert path.read_text(encoding="utf-8") == "{not json"
    assert any("not valid JSON" in problem for problem in check(project, home))


def test_configured_skill_directories_are_copied_into_a_linked_plugin(project, home):
    skill(project / "tools" / "skills", "extra")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["tools/skills"]\n', encoding="utf-8"
    )

    assert sync(project, home)

    linked = project / ".meow" / "linked" / "plugin"
    assert (linked / "skills" / "extra" / "SKILL.md").is_file()
    manifest = linked / ".claude-plugin" / "plugin.json"
    assert json.loads(manifest.read_text())["name"] == "meow-project-linked"
    shared = settings(project / ".claude" / "settings.json")
    assert shared["extraKnownMarketplaces"]["meow-project-linked"]["source"] == {
        "source": "directory",
        "path": "./.meow/linked",
    }
    assert check(project, home) == []


def test_edited_configured_skill_is_reported_stale_then_resynced(project, home):
    source = project / "tools" / "skills"
    skill(source, "extra")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["tools/skills"]\n', encoding="utf-8"
    )
    sync(project, home)

    (source / "extra" / "SKILL.md").write_text(
        "---\nname: extra\ndescription: Changed.\n---\n\nNew.\n", encoding="utf-8"
    )
    assert any("out of date" in problem for problem in check(project, home))

    sync(project, home)

    copied = project / ".meow" / "linked" / "plugin" / "skills" / "extra" / "SKILL.md"
    assert "New." in copied.read_text(encoding="utf-8")
    assert check(project, home) == []


def test_removed_configured_skill_leaves_the_linked_plugin(project, home):
    source = project / "tools" / "skills"
    skill(source, "extra")
    skill(source, "other")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["tools/skills"]\n', encoding="utf-8"
    )
    sync(project, home)
    (source / "other" / "SKILL.md").unlink()
    (source / "other").rmdir()

    sync(project, home)

    skills = project / ".meow" / "linked" / "plugin" / "skills"
    assert sorted(path.name for path in skills.iterdir()) == ["extra"]


def test_config_entry_for_the_conventional_folder_is_not_copied(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = [".meow/plugin/skills"]\n', encoding="utf-8"
    )

    sync(project, home)

    assert not (project / ".meow" / "linked").exists()


def test_user_scope_configured_skills_use_the_user_plugin_dirs(project, home):
    skill(home / "shared", "personal")
    (home / ".meow" / "config.toml").write_text(
        f'[custom]\nskills = ["{(home / "shared").as_posix()}"]\n', encoding="utf-8"
    )

    sync(project, home, ("user",))

    linked = home / ".meow" / "linked" / "plugin"
    assert (linked / "skills" / "personal" / "SKILL.md").is_file()
    entries = settings(home / ".claude" / "settings.json")["env"][
        "CLAUDE_CODE_PLUGIN_DIRS"
    ]
    assert str(linked.resolve()) in entries


def test_unregistered_skills_are_reported_without_changes(project, home):
    skill(project / ".meow" / "local" / "plugin" / "skills", "mine")

    problems = check(project, home)

    assert problems
    assert "not registered" in problems[0]
    assert not (project / ".claude").exists()


def test_skills_sync_command_registers_and_check_flags_drift(project, home, capsys):
    from meow.cli.parser import _build_arg_parser
    from meow.cli.skills_cli import skills_command

    skill(project / ".meow" / "local" / "plugin" / "skills", "mine")
    parser = _build_arg_parser()

    with pytest.raises(ValueError, match="meow skills sync"):
        skills_command(parser.parse_args(["skills", "sync", "--check"]), project)

    skills_command(parser.parse_args(["skills", "sync", "--scope", "local"]), project)
    assert "restart Claude Code" in capsys.readouterr().out

    skills_command(parser.parse_args(["skills", "sync", "--check"]), project)
    assert "are registered" in capsys.readouterr().out


def test_user_plugin_enabled_through_marketplace_counts_as_registered(project, home):
    skill(home / ".meow" / "plugin" / "skills", "everywhere")
    path = home / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({
            "extraKnownMarketplaces": {"meow-user": {}},
            "enabledPlugins": {"meow-user@meow-user": True},
        }),
        encoding="utf-8",
    )

    assert check(project, home) == []


def test_project_plugin_enabled_in_local_settings_counts_as_registered(project, home):
    skill(project / ".meow" / "plugin" / "skills", "team")
    path = project / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({
            "extraKnownMarketplaces": {"meow-project": {}},
            "enabledPlugins": {"meow-project@meow-project": True},
        }),
        encoding="utf-8",
    )

    assert check(project, home) == []


def test_hidden_files_do_not_make_linked_copies_stale(project, home):
    skill(project / "shared", "linked-one")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["shared"]\n', encoding="utf-8"
    )
    sync(project, home)
    assert check(project, home) == []

    linked = project / ".meow" / "linked" / "plugin" / "skills"
    (linked / ".DS_Store").write_bytes(b"x")

    assert check(project, home) == []

"""Registering custom skills with Claude Code at every scope."""

import json
import os
import shutil
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


def plugin(project: Path, scope: str) -> Path:
    return project / ".claude" / "skills" / f"meow-{scope}"


def published(project: Path, name: str) -> Path:
    return project / ".claude" / "skills" / name


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


def manifest(root: Path) -> dict:
    return settings(root / ".claude-plugin" / "plugin.json")


def test_hand_made_skill_in_every_scope_is_published_natively(project, home):
    skill(plugin(project, "local") / "skills", "mine")
    skill(plugin(project, "project") / "skills", "team")
    skill(home / ".meow" / "plugin" / "skills", "everywhere")

    assert sync(project, home)

    # Claude Code watches these folders, so no restart or settings entry.
    assert (published(project, "mine") / "SKILL.md").is_file()
    assert (published(project, "team") / ".meow-managed").read_text().strip() == (
        "project"
    )
    assert (home / ".claude" / "skills" / "everywhere" / "SKILL.md").is_file()
    assert not (home / ".claude" / "settings.json").exists()
    assert not (project / ".claude" / "settings.json").exists()
    assert check(project, home) == []


def test_sync_is_idempotent_and_keeps_existing_settings(project, home):
    skill(home / ".meow" / "plugin" / "skills", "everywhere")
    path = home / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"model": "x"}), encoding="utf-8")

    assert sync(project, home)
    first = path.read_text(encoding="utf-8")

    assert sync(project, home) == []
    assert path.read_text(encoding="utf-8") == first
    assert settings(path)["model"] == "x"


def test_scopes_without_skills_are_left_alone(project, home):
    assert sync(project, home) == []
    assert not (project / ".claude").exists()
    assert not (home / ".claude").exists()


def test_scope_option_limits_what_is_registered(project, home):
    skill(plugin(project, "project") / "skills", "team")
    skill(home / ".meow" / "plugin" / "skills", "everywhere")

    sync(project, home, ("user",))

    assert (home / ".claude" / "skills" / "everywhere").is_dir()
    assert not published(project, "team").exists()


def test_malformed_settings_are_not_overwritten(project, home):
    skill(home / ".meow" / "plugin" / "skills", "everywhere")
    path = home / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        sync(project, home)

    assert path.read_text(encoding="utf-8") == "{not json"
    assert any("not valid JSON" in problem for problem in check(project, home))


def test_configured_skill_directories_are_published_natively(project, home):
    skill(project / "tools" / "skills", "extra")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["tools/skills"]\n', encoding="utf-8"
    )

    assert sync(project, home)

    assert (published(project, "extra") / "SKILL.md").is_file()
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
    assert any("not published" in problem for problem in check(project, home))

    sync(project, home)

    assert "New." in (published(project, "extra") / "SKILL.md").read_text()
    assert check(project, home) == []


def test_removed_skill_loses_only_its_own_published_copy(project, home):
    source = project / "tools" / "skills"
    skill(source, "extra")
    skill(source, "other")
    skill(project / ".claude" / "skills", "handmade")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = ["tools/skills"]\n', encoding="utf-8"
    )
    sync(project, home)
    (source / "other" / "SKILL.md").unlink()
    (source / "other").rmdir()

    sync(project, home)

    assert published(project, "extra").is_dir()
    assert not published(project, "other").exists()
    assert (published(project, "handmade") / "SKILL.md").is_file()


def test_config_entry_for_the_conventional_folder_publishes_once(project, home):
    skill(plugin(project, "project") / "skills", "team")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nskills = [".claude/skills/meow-project/skills"]\n',
        encoding="utf-8",
    )

    sync(project, home)

    assert (published(project, "team") / "SKILL.md").is_file()
    assert check(project, home) == []


def test_user_scope_configured_skills_are_published_to_the_user_folder(project, home):
    skill(home / "shared", "personal")
    (home / ".meow" / "config.toml").write_text(
        f'[custom]\nskills = ["{(home / "shared").as_posix()}"]\n', encoding="utf-8"
    )

    sync(project, home, ("user",))

    assert (home / ".claude" / "skills" / "personal" / "SKILL.md").is_file()
    assert check(project, home) == []


def test_the_old_user_plugin_directory_entry_is_removed(project, home):
    skill(home / ".meow" / "plugin" / "skills", "everywhere")
    path = home / ".claude" / "settings.json"
    path.parent.mkdir()
    plugin_dir = str((home / ".meow" / "plugin").resolve())
    other = "/elsewhere/plugin"
    path.write_text(
        json.dumps({
            "model": "x",
            "env": {"CLAUDE_CODE_PLUGIN_DIRS": f"{plugin_dir}{os.pathsep}{other}"},
        }),
        encoding="utf-8",
    )

    assert any("no longer needed" in line for line in check(project, home))
    sync(project, home)

    data = settings(path)
    assert data["model"] == "x"
    assert data["env"]["CLAUDE_CODE_PLUGIN_DIRS"] == other
    assert check(project, home) == []


def test_unpublished_skills_are_reported_without_changes(project, home):
    skill(plugin(project, "local") / "skills", "mine")

    problems = check(project, home)

    assert problems
    assert "not published" in problems[0]
    assert not published(project, "mine").exists()


def test_skills_sync_command_registers_and_check_flags_drift(project, home, capsys):
    from meow.cli.parser import _build_arg_parser
    from meow.cli.skills_cli import skills_command

    skill(plugin(project, "local") / "skills", "mine")
    parser = _build_arg_parser()

    with pytest.raises(ValueError, match="meow skills sync"):
        skills_command(parser.parse_args(["skills", "sync", "--check"]), project)

    skills_command(parser.parse_args(["skills", "sync", "--scope", "local"]), project)
    assert "/reload-plugins" in capsys.readouterr().out

    skills_command(parser.parse_args(["skills", "sync", "--check"]), project)
    assert "are registered" in capsys.readouterr().out


def test_user_plugin_enabled_through_marketplace_counts_as_registered(project, home):
    skill(home / ".meow" / "plugin" / "skills", "everywhere")
    sync(project, home, ("user",))
    path = home / ".claude" / "settings.json"
    path.write_text(
        json.dumps({
            "extraKnownMarketplaces": {
                "meow-user": {
                    "source": {"source": "directory", "path": str(home / ".meow")}
                }
            },
            "enabledPlugins": {"meow-user@meow-user": True},
        }),
        encoding="utf-8",
    )

    assert check(project, home) == []


def test_hidden_files_do_not_make_published_copies_stale(project, home):
    skill(home / "shared", "linked-one")
    (home / ".meow" / "config.toml").write_text(
        f'[custom]\nskills = ["{(home / "shared").as_posix()}"]\n', encoding="utf-8"
    )
    sync(project, home, ("user",))
    assert check(project, home) == []

    (home / ".claude" / "skills" / "linked-one" / ".DS_Store").write_bytes(b"x")

    assert check(project, home) == []


def test_deleted_user_copy_is_restored(project, home):
    skill(home / ".meow" / "plugin" / "skills", "everywhere")
    sync(project, home)
    shutil.rmtree(home / ".claude" / "skills" / "everywhere")

    assert any("not published" in problem for problem in check(project, home))
    sync(project, home)
    assert check(project, home) == []


def test_deleted_published_copy_is_restored(project, home):
    skill(plugin(project, "project") / "skills", "team")
    sync(project, home)
    shutil.rmtree(published(project, "team"))

    assert any("not published" in problem for problem in check(project, home))
    sync(project, home)
    assert check(project, home) == []


def test_a_skill_the_user_wrote_is_never_replaced(project, home):
    own = project / ".claude" / "skills" / "team"
    skill(project / ".claude" / "skills", "team")
    skill(plugin(project, "project") / "skills", "team")

    problems = sync(project, home)

    assert any("not MEOW's copy" in problem for problem in problems)
    assert not (own / ".meow-managed").exists()


def test_local_copies_are_excluded_from_git_without_touching_gitignore(project, home):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    skill(plugin(project, "local") / "skills", "mine")

    sync(project, home)

    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert "/.claude/skills/mine/" in exclude
    assert not (project / ".gitignore").exists()
    skill(plugin(project, "local") / "skills", "mine2")
    shutil.rmtree(plugin(project, "local") / "skills" / "mine")
    sync(project, home)
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert "/.claude/skills/mine2/" in exclude
    assert "/.claude/skills/mine/" not in exclude


def test_old_linked_layout_is_set_aside_not_deleted(project):
    from meow.project.custom.legacy import legacy_layout

    old = project / ".meow" / "linked" / "plugin" / "skills"
    skill(old, "mine")

    legacy_layout(project, apply=True)

    assert not (project / ".meow" / "linked").exists()
    kept = project / ".meow" / "linked.bak" / "plugin" / "skills" / "mine"
    assert (kept / "SKILL.md").is_file()

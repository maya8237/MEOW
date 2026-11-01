"""Migrating project and local plugins out of the old marketplace layout."""

import json
import subprocess
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.custom.registration import check_registration, old_layout
from meow.project.onboarding import BOUNDARY, onboard_project


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


def old_plugin(base: Path, name: str, skills: tuple[str, ...] = ()) -> None:
    """A scope plugin and its marketplace as earlier versions wrote them."""
    write(
        base / "plugin" / ".claude-plugin" / "plugin.json", json.dumps({"name": name})
    )
    write(base / ".claude-plugin" / "marketplace.json", json.dumps({"name": name}))
    for item in skills:
        skill(base / "plugin" / "skills", item)


def entry(path: str) -> dict:
    return {"source": {"source": "directory", "path": path}}


def names(project: Path) -> set[str]:
    return {item.name for item in load_config(project)["customizations"].skills}


def old_settings(project: Path) -> None:
    write(
        project / ".claude" / "settings.json",
        json.dumps({
            "model": "keep",
            "extraKnownMarketplaces": {
                "meow-project": entry("./.meow"),
                "other": entry("/elsewhere"),
            },
            "enabledPlugins": {"meow-project@meow-project": True, "o@other": True},
        }),
    )
    write(
        project / ".claude" / "settings.local.json",
        json.dumps({
            "extraKnownMarketplaces": {"meow-local": entry("./.meow/local")},
            "enabledPlugins": {"meow-local@meow-local": True},
        }),
    )


def test_old_layout_is_not_read_until_migrated(project, home):
    old_plugin(project / ".meow", "meow-project", ("team",))

    assert names(project) == set()
    assert any("old layout" in line for line in old_layout(project))


def test_migration_moves_plugins_and_removes_old_marketplaces(project, home):
    old_plugin(project / ".meow", "meow-project", ("team",))
    old_plugin(project / ".meow" / "local", "meow-local", ("mine",))
    write(project / ".meow" / "linked" / "plugin" / "skills" / "x" / "SKILL.md", "")
    old_settings(project)

    assert old_layout(project, apply=True)

    skills = project / ".claude" / "skills"
    assert (skills / "meow-project" / "skills" / "team" / "SKILL.md").is_file()
    assert (skills / "meow-local" / "skills" / "mine" / "SKILL.md").is_file()
    for gone in ("plugin", ".claude-plugin", "linked", "local/plugin"):
        assert not (project / ".meow" / gone).exists(), gone
    assert json.loads((project / ".claude" / "settings.json").read_text()) == {
        "model": "keep",
        "extraKnownMarketplaces": {"other": entry("/elsewhere")},
        "enabledPlugins": {"o@other": True},
    }
    assert not (project / ".claude" / "settings.local.json").exists()
    assert names(project) == {"team", "mine"}
    assert old_layout(project) == []
    custom = load_config(project)["customizations"]
    assert [p for p in check_registration(custom, project, home) if "old" in p] == []


def test_config_entries_naming_old_folders_are_rewritten(project, home):
    old_plugin(project / ".meow", "meow-project", ("team",))
    old_plugin(project / ".meow" / "local", "meow-local", ("mine",))
    write(
        project / ".meow" / "config.toml",
        '# keep me\n[custom]\nskills = ["./.meow/plugin/skills/"]\n',
    )
    write(
        project / ".meow" / "config.local.toml",
        '[custom]\nskills = [{ path = ".meow/local/plugin/skills", '
        'roles = ["generator"] }]\n',
    )

    old_layout(project, apply=True)

    text = (project / ".meow" / "config.toml").read_text()
    assert "# keep me" in text
    assert '".claude/skills/meow-project/skills"' in text
    local = (project / ".meow" / "config.local.toml").read_text()
    assert 'path = ".claude/skills/meow-local/skills", roles = ["generator"]' in local
    assert names(project) == {"team", "mine"}


def test_old_hooks_json_keeps_hand_written_entries(project, home):
    old_plugin(project / ".meow", "meow-project")
    mine = {"hooks": [{"type": "command", "command": "echo mine"}]}
    write(
        project / ".meow" / "plugin" / "hooks" / "hooks.json",
        json.dumps({"hooks": {"Stop": [mine]}}),
    )

    old_layout(project, apply=True)

    new = project / ".claude" / "skills" / "meow-project" / "hooks" / "hooks.json"
    assert json.loads(new.read_text())["hooks"]["Stop"] == [mine]


def test_name_taken_in_the_new_folder_is_left_behind_and_reported(project, home):
    old_plugin(project / ".meow", "meow-project", ("team",))
    skill(project / ".claude" / "skills" / "meow-project" / "skills", "team")

    lines = old_layout(project, apply=True)

    assert any("kept team" in line for line in lines)
    assert (project / ".meow" / "plugin" / "skills" / "team").is_dir()


def test_any_meow_command_migrates_through_onboarding(project, home):
    old_plugin(project / ".meow", "meow-project", ("team",))
    old = [rule for rule in BOUNDARY if rule != "!.meow/hooks/"] + ["!.meow/plugin/"]
    (project / ".gitignore").write_text("\n".join(old) + "\n", encoding="utf-8")

    report = onboard_project(project)

    assert report.error is None
    assert ".claude/skills" in report.files
    assert "!.meow/plugin/" not in (project / ".gitignore").read_text()
    assert names(project) == {"team"}


def test_personal_plugins_stay_out_of_git(project, home):
    skills = project / ".claude" / "skills"
    for name in ("meow-local", "meow-project-linked", "meow-project"):
        write(skills / name / "skills" / "a" / "SKILL.md", "")

    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall", ".claude"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "meow-project/skills/a" in status
    assert "meow-local" not in status
    assert "meow-project-linked" not in status


def test_sync_commands_migrate_before_loading_config(project, home, capsys):
    from meow.cli.parser import _build_arg_parser
    from meow.cli.skills_cli import skills_command
    from meow.cli.sync_cli import sync_command

    old_plugin(project / ".meow", "meow-project", ("team",))
    write(
        project / ".meow" / "config.toml",
        '[custom]\nskills = [".meow/plugin/skills"]\n',
    )
    parse = _build_arg_parser().parse_args

    skills_command(parse(["skills", "sync"]), project)
    assert (project / ".claude/skills/meow-project/skills/team/SKILL.md").is_file()

    old_plugin(project / ".meow" / "local", "meow-local", ("mine",))
    sync_command(parse(["sync", "--auto"]), project)
    message = json.loads(capsys.readouterr().out.splitlines()[-1])["systemMessage"]
    assert "moved from" in message
    assert (project / ".claude/skills/meow-local/skills/mine/SKILL.md").is_file()

"""Upgrading from TOML agents: every agent you own becomes Markdown, unnoticed."""

import shutil
import subprocess
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.custom.diagnostics import drift_message
from meow.project.custom.registration import old_layout
from meow.project.custom.rendering import builtin_agent

FIXTURES = Path(__file__).parent / "fixtures"
HANDWRITTEN = """\
description = "Finds TODOs."
tools = ["Read", "Grep"]
session = "persistent"
lint_on_edit = true
prompt = '''
List the TODOs in {{active_dir}}. {{> tone}}
'''

[fragments]
tone = "Be brief."
"""
FIELDS = (
    "description",
    "prompt",
    "tools",
    "model",
    "skills",
    "agents",
    "session",
    "lint_on_edit",
    "variants",
    "fragments",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    (user_home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    return user_home


@pytest.fixture
def project(tmp_path, home):
    root = tmp_path / "project"
    (root / ".meow" / "agents").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _content(agent):
    """Fields that decide behaviour; a TOML prompt's outer newlines do not."""
    return tuple(
        value.strip() if isinstance(value, str) else value
        for value in (getattr(agent, key) for key in FIELDS)
        if not isinstance(value, dict)
    ) + tuple(
        {name: text.strip() for name, text in getattr(agent, key).items()}
        for key in ("variants", "fragments")
    )


def _agents(project):
    return load_config(project)["customizations"]


def test_a_handwritten_toml_agent_becomes_identical_markdown(project):
    path = project / ".meow" / "agents" / "todo.toml"
    path.write_text(HANDWRITTEN, encoding="utf-8")
    before = _agents(project).agent("todo")

    assert old_layout(project, apply=True) == [
        f"migrated {path} -> todo.md",
    ]

    after = _agents(project).agent("todo")
    assert not path.exists()
    assert after.file == path.with_suffix(".md")
    assert _content(after) == _content(before)


def test_a_dry_run_reports_and_changes_nothing(project):
    path = project / ".meow" / "agents" / "todo.toml"
    path.write_text(HANDWRITTEN, encoding="utf-8")

    assert old_layout(project) == [f"will migrate {path} -> todo.md"]
    assert path.read_text(encoding="utf-8") == HANDWRITTEN
    assert not path.with_suffix(".md").exists()


@pytest.mark.parametrize("name", ["planner", "reviewer"])
def test_an_unmodified_eject_from_0_1_42_reports_no_drift(project, capsys, name):
    shutil.copy(
        FIXTURES / f"ejected_{name}_0_1_42.toml",
        project / ".meow" / "agents" / f"{name}.toml",
    )

    old_layout(project, apply=True)

    agent = _agents(project).agent(name)
    assert agent.file.suffix == ".md"
    assert drift_message(agent, "agents") is None
    assert _content(agent) == _content(builtin_agent(name))


def test_an_edited_eject_keeps_its_origin_and_reports_drift(project):
    text = (FIXTURES / "ejected_planner_0_1_42.toml").read_text(encoding="utf-8")
    path = project / ".meow" / "agents" / "planner.toml"
    path.write_text(text.replace("sprint plan", "SPRINT PLAN", 1), encoding="utf-8")

    old_layout(project, apply=True)

    agent = _agents(project).agent("planner")
    assert "builtin:planner@fd6f0a27" in agent.ejected_from
    assert "changed" in drift_message(agent, "agents")


def test_every_scope_you_own_is_migrated(project, home):
    folders = {
        "local-todo": project / ".meow" / "local" / "agents",
        "user-todo": home / ".meow" / "agents",
        "team-todo": project / "team-agents",
    }
    for name, folder in folders.items():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.toml").write_text(HANDWRITTEN, encoding="utf-8")
    (project / ".meow" / "config.toml").write_text(
        '[custom]\nagents = ["team-agents"]\n', encoding="utf-8"
    )

    old_layout(project, apply=True)

    for name, folder in folders.items():
        assert not (folder / f"{name}.toml").exists()
        assert (folder / f"{name}.md").is_file()
    assert set(folders) <= {agent.name for agent in _agents(project).catalog}


def test_a_markdown_agent_of_the_same_name_is_never_overwritten(project):
    folder = project / ".meow" / "agents"
    (folder / "todo.toml").write_text(HANDWRITTEN, encoding="utf-8")
    (folder / "todo.md").write_text(
        "---\nname: todo\ndescription: Mine.\n---\nMine.\n", encoding="utf-8"
    )

    old_layout(project, apply=True)

    assert (folder / "todo.toml").exists()
    assert "Mine." in (folder / "todo.md").read_text(encoding="utf-8")


def test_a_broken_toml_agent_is_left_alone(project):
    path = project / ".meow" / "agents" / "broken.toml"
    path.write_text("not = [valid", encoding="utf-8")

    assert old_layout(project, apply=True) == []
    assert path.read_text(encoding="utf-8") == "not = [valid"


def test_the_retired_claude_table_is_deleted_and_the_rest_kept(project):
    config = project / ".meow" / "config.toml"
    config.write_text(
        '# my settings\n[claude]\nagents = ["reviewer"]\n\n[run]\nmax_rounds = 4\n',
        encoding="utf-8",
    )

    assert old_layout(project, apply=True) == [
        f"{config}: removed retired [claude] table"
    ]

    text = config.read_text(encoding="utf-8")
    assert "[claude]" not in text
    assert "# my settings" in text and "max_rounds = 4" in text
    assert old_layout(project) == []

"""TOML agent files, conventional directories, and the builtin scope."""

import pytest

from meow.project.config import load_config
from meow.project.custom import builtin_dir


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


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


FULL = """\
description = "Audits security."
model = "haiku"
tools = ["Read", "Grep"]
skills = ["superpowers:brainstorming"]
agents = ["explorer"]
session = "persistent"
lint_on_edit = true
ejected_from = "builtin:auditor@abc"
prompt = '''
Audit {{plan_file}}.
'''

[variants]
quick = "Skim {{plan_file}}."

[fragments]
tail = "Be brief."
"""

MINIMAL = 'description = "d"\nprompt = "p"\n'


def test_toml_agent_loads_every_field(project, home):
    write(project / ".meow" / "agents" / "auditor.toml", FULL)

    found = load_config(project)["customizations"].agent("auditor")

    assert found.description == "Audits security."
    assert found.model == "haiku"
    assert found.tools == ("Read", "Grep")
    assert found.skills == ("superpowers:brainstorming",)
    assert found.agents == ("explorer",)
    assert found.session == "persistent"
    assert found.lint_on_edit is True
    assert found.ejected_from == "builtin:auditor@abc"
    assert found.prompt.strip() == "Audit {{plan_file}}."
    assert dict(found.variants) == {"quick": "Skim {{plan_file}}."}
    assert dict(found.fragments) == {"tail": "Be brief."}
    assert found.source.scope == "project"
    assert found.role == "auditor"


def test_role_maps_hyphens_to_underscores(project, home):
    write(project / ".meow" / "agents" / "review-fixer.toml", MINIMAL)

    found = load_config(project)["customizations"].agent("review-fixer")

    assert found.role == "review_fixer"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('prompt = "p"\n', "description"),
        ('description = "d"\n', "prompt"),
        (MINIMAL + 'session = "forever"\n', "session"),
        (MINIMAL + 'colour = "red"\n', "unknown key"),
        (MINIMAL + 'tools = "Read"\n', "tools"),
        (MINIMAL + 'name = "other"\n', "must match"),
        ("description = \n", "auditor.toml"),
    ],
)
def test_invalid_toml_agents_name_the_file(project, home, text, message):
    write(project / ".meow" / "agents" / "auditor.toml", text)

    with pytest.raises(ValueError, match=message):
        load_config(project)


def test_builtin_scope_is_always_present(project, home):
    custom = load_config(project)["customizations"]

    explorer = custom.agent("explorer")
    assert explorer.source.scope == "builtin"
    assert explorer.file.parent == builtin_dir() / "agents"


def test_local_conventional_directory_overrides_builtin(project, home):
    write(
        project / ".meow" / "local" / "agents" / "explorer.toml",
        'description = "Mine."\nprompt = "Explore my way."\n',
    )

    custom = load_config(project)["customizations"]

    assert custom.agent("explorer").prompt == "Explore my way."
    assert "agent explorer: local overrides builtin" in custom.overrides


def test_user_conventional_directory_is_read(project, home):
    write(home / ".meow" / "agents" / "helper.toml", MINIMAL)

    assert load_config(project)["customizations"].agent("helper").source.scope == "user"


def test_conventional_agents_are_not_delegates(project, home):
    write(project / ".meow" / "agents" / "helper.toml", MINIMAL)

    custom = load_config(project)["customizations"]

    assert custom.agents_for("planner") == []
    assert custom.agents == ()


def test_custom_entry_pointing_at_conventional_directory_counts_once(project, home):
    write(project / ".meow" / "agents" / "helper.toml", MINIMAL)
    write(project / ".meow" / "config.toml", '[custom]\nagents = [".meow/agents"]\n')

    custom = load_config(project)["customizations"]

    assert [agent.name for agent in custom.agents_for("planner")] == ["helper"]


def test_custom_entry_overriding_a_builtin_is_not_a_delegate(project, home):
    write(project / "agents" / "explorer.md", "---\ndescription: d\n---\n\nMine.\n")
    write(project / ".meow" / "config.toml", '[custom]\nagents = ["agents"]\n')

    custom = load_config(project)["customizations"]

    assert custom.agent("explorer").prompt == "Mine."
    assert custom.agents_for("planner") == []


def test_unknown_agent_lookup_names_the_agent(project, home):
    with pytest.raises(KeyError, match="no-such-agent"):
        load_config(project)["customizations"].agent("no-such-agent")

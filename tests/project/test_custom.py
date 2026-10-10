"""Custom skills and agents: config scopes, discovery, SDK wiring, native output."""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from meow.agents import generator, planner
from meow.agents.base import ProjectContext
from meow.agents.explorer import make_explorer_agent
from meow.execution.sprint import Sprint
from meow.native import native
from meow.project.config import load_config
from meow.project.custom import PLUGIN_NAME, skill_plugin_dir


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


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def skill(directory: Path, name: str, description: str = "Does a thing.") -> Path:
    return write(
        directory / name / "SKILL.md",
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody of {name}.\n",
    )


def agent(directory: Path, name: str, extra: str = "", body: str = "Be helpful."):
    return write(
        directory / f"{name}.md",
        f"---\nname: {name}\ndescription: The {name} agent.\n{extra}---\n\n{body}\n",
    )


def test_no_custom_table_yields_empty_customizations(project, home):
    custom = load_config(project)["customizations"]

    assert custom.skills == ()
    assert custom.agents == ()


def test_three_scopes_resolve_paths_from_their_own_layer(project, home, tmp_path):
    skill(project / "meow-ext" / "skills", "team-style")
    skill(project / "private" / "skills", "my-notes")
    elsewhere = tmp_path / "anywhere" / "skills"
    skill(elsewhere, "personal")
    skill(home / ".meow" / "skills", "relative-to-user")
    write(
        project / ".meow" / "config.toml",
        '[custom]\nskills = ["meow-ext/skills"]\n',
    )
    write(
        project / ".meow" / "config.local.toml",
        '[custom]\nskills = [{ path = "private/skills", roles = ["planner"] }]\n',
    )
    write(
        home / ".meow" / "config.toml",
        "[custom]\nskills = [" + json.dumps(elsewhere.as_posix()) + ', "skills"]\n',
    )

    custom = load_config(project)["customizations"]

    scopes = {s.name: s.source.scope for s in custom.skills}
    assert scopes == {
        "my-notes": "local",
        "team-style": "project",
        "personal": "user",
        "relative-to-user": "user",
    }
    assert f"{PLUGIN_NAME}:my-notes" in custom.skills_for("planner")
    assert f"{PLUGIN_NAME}:my-notes" not in custom.skills_for("reviewer")
    assert f"{PLUGIN_NAME}:team-style" in custom.skills_for("reviewer")


def test_higher_scope_overrides_same_name(project, home):
    skill(project / "shared", "style", "Project style.")
    skill(project / "mine", "style", "My style.")
    agent(home / ".meow" / "agents", "auditor", body="User auditor.")
    agent(project / "agents", "auditor", body="Project auditor.")
    write(
        project / ".meow" / "config.toml",
        '[custom]\nskills = ["shared"]\nagents = ["agents"]\n',
    )
    write(project / ".meow" / "config.local.toml", '[custom]\nskills = ["mine"]\n')
    write(home / ".meow" / "config.toml", '[custom]\nagents = ["agents"]\n')

    custom = load_config(project)["customizations"]

    assert [s.description for s in custom.skills] == ["My style."]
    assert [a.prompt for a in custom.agents] == ["Project auditor."]
    assert "skill style: local overrides project" in custom.overrides
    assert "agent auditor: project overrides user" in custom.overrides


def test_duplicate_name_within_one_scope_is_an_error(project, home):
    skill(project / "a", "style")
    skill(project / "b", "style")
    write(project / ".meow" / "config.toml", '[custom]\nskills = ["a", "b"]\n')

    with pytest.raises(ValueError, match="defined twice in project scope"):
        load_config(project)


@pytest.mark.parametrize("path", ["../outside", "/abs/path", "~/skills"])
def test_project_scope_paths_must_stay_in_the_repository(project, home, path):
    write(
        project / ".meow" / "config.toml",
        "[custom]\nskills = [" + json.dumps(path) + "]\n",
    )

    with pytest.raises(ValueError, match="must be relative to the repository"):
        load_config(project)


def test_project_scope_path_must_not_be_git_ignored(project, home):
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    write(project / ".gitignore", "ignored/\n")
    skill(project / "ignored", "style")
    write(project / ".meow" / "config.toml", '[custom]\nskills = ["ignored"]\n')

    with pytest.raises(ValueError, match="ignored by git"):
        load_config(project)

    write(project / ".meow" / "config.toml", "")
    write(project / ".meow" / "config.local.toml", '[custom]\nskills = ["ignored"]\n')
    assert load_config(project)["customizations"].skills[0].source.scope == "local"


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('[custom]\nskills = ["missing"]\n', "does not exist"),
        ("[custom]\nprompts = []\n", "unknown key"),
        ('[custom]\nagents = [{ path = "x", roles = ["reviewer"] }]\n', "unknown role"),
        ('[custom]\nskills = [{ path = "x", extra = 1 }]\n', "unknown key"),
    ],
)
def test_invalid_custom_tables_are_rejected(project, home, table, message):
    (project / "x").mkdir()
    write(project / ".meow" / "config.toml", table)

    with pytest.raises(ValueError, match=message):
        load_config(project)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no frontmatter", "must start with"),
        ("---\nname: style\n---\n", "description"),
        ("---\nname: Other\ndescription: d\n---\n", "lowercase"),
        ("---\nname: other\ndescription: d\n---\n", "must match its directory"),
    ],
)
def test_invalid_skill_files_are_rejected(project, home, text, message):
    write(project / "skills" / "style" / "SKILL.md", text)
    write(project / ".meow" / "config.toml", '[custom]\nskills = ["skills"]\n')

    with pytest.raises(ValueError, match=message):
        load_config(project)


def test_agent_frontmatter_follows_claude_code_format(project, home):
    skill(project / "skills", "house-style")
    agent(
        project / "agents",
        "security-auditor",
        "tools: Read, Grep, Edit\nmodel: haiku\nskills:\n  - house-style\n"
        "  - superpowers:brainstorming\ncolor: red\n",
        body="Audit for injection bugs.",
    )
    write(
        project / ".meow" / "config.toml",
        '[custom]\nskills = ["skills"]\n'
        'agents = [{ path = "agents", roles = ["generator"] }]\n',
    )

    custom = load_config(project)["customizations"]

    (found,) = custom.agents
    assert found.tools == ("Read", "Grep", "Edit")
    assert found.model == "haiku"
    assert found.skills == (f"{PLUGIN_NAME}:house-style", "superpowers:brainstorming")
    assert found.prompt == "Audit for injection bugs."
    assert custom.agents_for("generator") == [found]
    assert custom.agents_for("planner") == []


def test_reserved_and_promptless_agents_are_rejected(project, home):
    write(project / ".meow" / "config.toml", '[custom]\nagents = ["agents"]\n')
    agent(project / "agents", "explorer")
    with pytest.raises(ValueError, match="reserved"):
        load_config(project)

    (project / "agents" / "explorer.md").unlink()
    agent(project / "agents", "empty", body="")
    with pytest.raises(ValueError, match="is the prompt"):
        load_config(project)


def test_skill_plugin_is_content_addressed_and_reused(project, home):
    skill(project / "skills", "style")
    write(project / "skills" / "style" / "scripts" / "check.py", "print(1)\n")
    write(project / ".meow" / "config.toml", '[custom]\nskills = ["skills"]\n')
    skills = load_config(project)["customizations"].skills

    first = skill_plugin_dir(skills)
    again = skill_plugin_dir(skills)
    skill(project / "skills", "style", "Version two.")
    changed = skill_plugin_dir(load_config(project)["customizations"].skills)

    manifest = json.loads((first / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == PLUGIN_NAME
    assert (first / "skills" / "style" / "scripts" / "check.py").is_file()
    assert first == again
    assert changed != first
    assert first.is_relative_to(home)
    assert skill_plugin_dir(()) is None


def _sprint(root: Path, config: dict) -> Sprint:
    config = {
        "models": {"explorer": None, "planner": None, "generator": None},
        "lint": [],
        "docs_dir": "plans",
        "max_rounds": 1,
        "lint_timeout": 60,
        **config,
    }
    return Sprint(
        repo_dir=root,
        config=config,
        explorer=make_explorer_agent(ProjectContext(root, config)),
        lint_hook=None,
        working_dir=root,
    )


def _custom_sprint(project: Path) -> Sprint:
    skill(project / "skills", "house-style")
    agent(project / "agents", "auditor", "tools: Read, Edit, Bash, Agent, WebFetch\n")
    agent(project / "agents", "helper")
    write(
        project / ".meow" / "config.toml",
        '[custom]\nskills = ["skills"]\nagents = ["agents"]\n',
    )
    return _sprint(project, {"customizations": load_config(project)["customizations"]})


def test_generator_receives_custom_skills_plugin_and_capped_agents(project, home):
    sprint = _custom_sprint(project)
    custom = sprint.config["customizations"]

    with patch("meow.agents.base.ClaudeSDKClient") as client:
        generator.Generator(sprint, project / "plan.md")
    options = client.call_args.kwargs["options"]

    plugin = {"type": "local", "path": str(skill_plugin_dir(custom.skills))}
    assert f"{PLUGIN_NAME}:house-style" in options.skills
    assert f"{PLUGIN_NAME}:house-style" in sprint.explorer.skills
    assert options.plugins == [plugin]
    assert set(options.agents) == {"explorer", "auditor", "helper"}
    assert options.agents["auditor"].tools == ["Read", "Edit", "Bash"]
    assert "Agent" not in options.agents["helper"].tools


def test_planner_custom_agents_are_capped_to_planner_tools(project, home):
    sprint = _custom_sprint(project)

    async def empty_query(*args, **kwargs):
        await asyncio.sleep(0)
        if False:
            yield None

    with (
        patch("meow.agents.base.query", side_effect=empty_query) as query,
        patch("meow.agents.planner.require_plan_file", side_effect=lambda p: p),
    ):
        asyncio.run(planner.PlannerAgent(sprint).run("f", "Request"))
    options = query.call_args.kwargs["options"]

    assert f"{PLUGIN_NAME}:house-style" in options.skills
    assert len(options.plugins) == 1
    assert options.agents["auditor"].tools == ["Read"]
    assert options.agents["helper"].tools == ["Read", "Grep", "Glob", "Write"]


def test_roles_without_customizations_are_unchanged(project, home):
    sprint = _sprint(project, {})
    with patch("meow.agents.base.ClaudeSDKClient") as client:
        generator.Generator(sprint, project / "plan.md")
    options = client.call_args.kwargs["options"]

    assert set(options.agents) == {"explorer"}
    assert options.plugins == []


def test_native_custom_lists_what_a_role_receives(project, home):
    skill(project / "skills", "house-style")
    agent(project / "agents", "auditor", "model: inherit\n")
    write(
        project / ".meow" / "config.toml",
        '[custom]\nskills = [{ path = "skills", roles = ["generator"] }]\n'
        'agents = [{ path = "agents", roles = ["planner"] }]\n',
    )

    generator_view = native.custom(project, project, "generator")
    planner_view = native.custom(project, project, "planner")
    everything = native.custom(project, project)

    assert [s["name"] for s in generator_view["skills"]] == ["house-style"]
    assert generator_view["skills"][0]["skill_file"].endswith("SKILL.md")
    assert generator_view["agents"] == []
    assert planner_view["skills"] == []
    (auditor,) = planner_view["agents"]
    assert auditor["prompt"] == "Be helpful."
    assert auditor["tools"] == "inherit"
    assert auditor["model"] is None
    assert auditor["scope"] == "project"
    assert len(everything["skills"]) == len(everything["agents"]) == 1

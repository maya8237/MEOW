"""MEOW's own runs must not delegate to the agents it synced for Claude Code.

The SDK defaults `setting_sources` to user and project once `skills` is set, so
`.claude/agents/` is read in a run; a deny rule per synced copy keeps them out.
"""

import subprocess

import pytest

from meow.agents.base import Agent, ProjectContext
from meow.project.config import load_config
from meow.project.custom.agent_sync import sync_agents

SCOUT = (
    'description = "Scout."\nclaude_agent = true\ntools = ["Read"]\n'
    'prompt = "Search {{active_dir}}."\n'
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    root = tmp_path / "project"
    (root / ".meow" / "agents").mkdir(parents=True)
    (root / ".meow" / "agents" / "scout.toml").write_text(SCOUT, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def run_options(root, **extra):
    config = load_config(root)
    custom = config["customizations"]
    sync_agents(custom.catalog, root, root / ".meow")
    return Agent(ProjectContext(root, config)).options(
        system_prompt="s", allowed_tools=["Read"], role="planner", **extra
    )


def test_synced_copies_are_denied_in_a_run(project):
    options = run_options(project, skills=["x"])

    assert "Agent(scout)" in options.disallowed_tools


def test_nothing_is_denied_when_nothing_was_synced(project):
    (project / ".meow" / "agents" / "scout.toml").unlink()

    assert run_options(project, skills=["x"]).disallowed_tools == []


def test_an_agent_the_run_passes_itself_is_not_denied(project):
    from claude_agent_sdk import AgentDefinition

    own = AgentDefinition(description="d", prompt="p", tools=["Read"])
    options = run_options(project, agents={"scout": own})

    assert "Agent(scout)" not in options.disallowed_tools


def test_existing_disallowed_tools_are_kept(project):
    options = run_options(project, disallowed_tools=["WebFetch"])

    assert options.disallowed_tools == ["WebFetch", "Agent(scout)"]

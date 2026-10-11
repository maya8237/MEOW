import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from meow.agents.base import Agent, ProjectContext
from meow.agents.planner import PlannerAgent
from meow.project.config import load_config


def project(tmp_path, definition, config=""):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "planner.toml").write_text(definition, encoding="utf-8")
    (tmp_path / ".meow" / "config.toml").write_text(config, encoding="utf-8")
    return ProjectContext(tmp_path, load_config(tmp_path))


@pytest.mark.parametrize("config,model", [("", "y"), ('[models]\nplanner = "x"', "x")])
def test_planner_override_and_config_model_precedence(tmp_path, config, model):
    context = project(
        tmp_path,
        'description = "Custom planner"\nmodel = "y"\ntools = ["Read", "Write"]\n'
        'skills = ["my-skill"]\nprompt = "Custom {{plan_file}}"',
        config,
    )
    captured = []

    async def query(request, options, role):  # ruff: ignore[unused-async] -- SDK boundary double
        captured.append(options)
        assert options.system_prompt.startswith("Custom ")
        Path(options.system_prompt.removeprefix("Custom ")).write_text("Plan")

    with patch.object(Agent, "run_query", side_effect=query):
        asyncio.run(PlannerAgent(context).run("feature", "request"))
    options = captured[0]
    assert options.model == model
    assert options.allowed_tools == ["Read", "Write"]
    assert options.tools == ["Read", "Write"]
    assert options.skills == ["my-skill"]
    assert options.agents == {}


def test_named_delegate_tools_are_capped(tmp_path):
    context = project(
        tmp_path,
        'description = "Planner"\ntools = ["Read", "Agent"]\n'
        'agents = ["explorer"]\nprompt = "Plan"',
    )
    role = Agent(context)
    definition = role.definition("planner")
    options = role.definition_options(definition, system_prompt="Plan")
    assert options.agents["explorer"].tools == ["Read"]


def test_unknown_delegate_names_its_parent_file(tmp_path):
    with pytest.raises(ValueError, match=r"planner.toml.*missing"):
        project(
            tmp_path,
            'description = "Planner"\nagents = ["missing"]\nprompt = "Plan"',
        )


@pytest.mark.parametrize("prompt", ["{{unknown}}", "{{> missing}}"])
def test_bad_agent_template_fails_during_config_loading(tmp_path, prompt):
    with pytest.raises(ValueError, match=r"planner.toml.*(unknown|missing)"):
        project(tmp_path, f'description = "Planner"\nprompt = "{prompt}"')


def test_permissions_accept_discovered_agent_roles(tmp_path):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "security-reviewer.toml").write_text(
        'description = "Security"\nprompt = "Review"', encoding="utf-8"
    )
    config = tmp_path / ".meow" / "config.toml"
    config.write_text(
        '[[permissions.rule]]\nrole = "security_reviewer"\n'
        'tool = "Read"\naction = "deny"',
        encoding="utf-8",
    )
    policy = load_config(tmp_path)["permissions"]
    assert policy.for_role("security_reviewer").decision("Read", {}) == "deny"
    config.write_text(config.read_text().replace("security_reviewer", "typo"))
    with pytest.raises(ValueError, match="typo"):
        load_config(tmp_path)

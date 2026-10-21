from pathlib import Path

import pytest

from meow.agents.base import Agent, ProjectContext
from meow.project.prompts import DEPENDENCY_GUIDANCE

SKILLS = Path(__file__).parents[2] / "skills"


def _options(tmp_path, tools):
    config = {"models": {"tester": "test-model"}, "lint": []}
    return Agent(ProjectContext(tmp_path, config)).options(
        system_prompt="role prompt", allowed_tools=tools, role="tester"
    )


def test_agents_with_bash_get_the_dependency_guidance(tmp_path):
    prompt = _options(tmp_path, ["Read", "Bash"]).system_prompt
    assert prompt == f"role prompt\n\n{DEPENDENCY_GUIDANCE}"
    assert "install_timeout" in prompt
    assert "Playwright" in prompt
    assert 'linker = \\"hoisted\\"' not in prompt
    assert 'linker = "hoisted"' in prompt


def test_agents_without_bash_keep_their_prompt(tmp_path):
    assert _options(tmp_path, ["Read"]).system_prompt == "role prompt"


@pytest.mark.parametrize("path", sorted(SKILLS.glob("*/SKILL.md")))
def test_every_skill_links_the_dependency_rules(path):
    assert "_shared/dependencies.md" in path.read_text(encoding="utf-8")


def test_shared_skill_rules_match_the_agent_rules():
    text = (SKILLS / "_shared" / "dependencies.md").read_text(encoding="utf-8")
    for word in ("install_timeout", "playwright", "hoisted", "globalStore"):
        assert word in text

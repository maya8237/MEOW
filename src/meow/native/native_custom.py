"""`meow native custom`: the custom skills and agents in effect for a role."""

from pathlib import Path

from meow.project.config import config_root, load_config
from meow.project.custom import AGENT_ROLES, Customizations, CustomSource


def _source(source: CustomSource) -> dict:
    return {
        "scope": source.scope,
        "directory": str(source.path),
        "config_file": str(source.config_file),
    }


def custom(working_dir: Path, active_dir: Path, role: str | None = None) -> dict:
    """List custom skills and agents, optionally only those a role receives.

    Native mode has no SDK plugin, so each skill is reported by its SKILL.md
    path for the session to read, and each agent carries the prompt, tools,
    and model to dispatch it with.
    """
    found = load_config(config_root(working_dir, active_dir))["customizations"]
    if not isinstance(found, Customizations):
        found = Customizations()
    skills = [s for s in found.skills if role is None or s.applies_to(role)]
    agents = [a for a in found.agents if role is None or a.applies_to(role)]
    return {
        "role": role,
        "skills": [
            {
                "name": skill.name,
                "qualified_name": skill.qualified_name,
                "description": skill.description,
                "skill_file": str(skill.directory / "SKILL.md"),
                "roles": list(skill.source.roles) or "all",
                **_source(skill.source),
            }
            for skill in skills
        ],
        "agents": [
            {
                "name": agent.name,
                "description": agent.description,
                "file": str(agent.file),
                "prompt": agent.prompt,
                "tools": list(agent.tools) if agent.tools is not None else "inherit",
                "model": agent.model,
                "skills": list(agent.skills),
                "roles": list(agent.source.roles) or sorted(AGENT_ROLES),
                **_source(agent.source),
            }
            for agent in agents
        ],
        "overrides": list(found.overrides),
    }

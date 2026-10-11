"""`meow native custom`: the custom skills and agents in effect for a role."""

from pathlib import Path

from meow.project.config import config_root, load_config
from meow.project.custom import AGENT_ROLES, Customizations, CustomSource
from meow.project.custom.workflows import expand, fingerprint, select_workflow


def _step_json(step):
    value = {"id": step.id}
    for name in ("when", "timeout", "task", "blocks", "max_rounds"):
        if (entry := getattr(step, name)) is not None:
            value[name] = entry
    value[step.kind] = (
        [_step_json(child) for child in step.body]
        if step.kind in {"loop", "parallel", "sequence"}
        else step.target
    )
    return value


def workflow(working_dir: Path, active_dir: Path, name: str) -> dict:
    config = load_config(config_root(working_dir, active_dir))
    item = select_workflow(config, name)
    catalog = config["customizations"]
    return {
        "name": item.name,
        "scope": item.source.scope,
        "description": item.description,
        "file": str(item.file),
        "fingerprint": fingerprint(item, catalog),
        "steps": [_step_json(step) for step in expand(item, catalog)],
    }


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
        "skipped": list(found.skipped),
    }

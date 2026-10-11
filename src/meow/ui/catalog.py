"""JSON view of every skill, agent, and workflow in effect for a project."""

from pathlib import Path

from meow.project.config import load_config


def _workflow(item) -> dict:
    return {
        "name": item.name,
        "description": item.description,
        "scope": item.source.scope,
        "file": str(item.file),
        "ejected": item.ejected_from is not None,
        "steps": [{"id": step.id, "kind": step.kind} for step in item.steps],
        "text": item.file.read_text(encoding="utf-8"),
    }


def _agent(item, delegates: set[str]) -> dict:
    return {
        "name": item.name,
        "role": item.role,
        "description": item.description,
        "scope": item.source.scope,
        "file": str(item.file),
        "ejected": item.ejected_from is not None,
        "delegate": item.name in delegates,
        "tools": None if item.tools is None else list(item.tools),
        "model": item.model,
        "skills": list(item.skills),
        "text": item.file.read_text(encoding="utf-8"),
    }


def _skill(item) -> dict:
    return {
        "name": item.name,
        "qualified_name": item.qualified_name,
        "description": item.description,
        "scope": item.source.scope,
        "directory": str(item.directory),
        "text": (item.directory / "SKILL.md").read_text(encoding="utf-8"),
    }


def _by_name(item) -> str:
    return item.name


def build_catalog(working_dir: Path) -> dict:
    try:
        custom = load_config(Path(working_dir))["customizations"]
    except (OSError, ValueError) as exc:
        return {
            "error": str(exc),
            "workflows": [],
            "agents": [],
            "skills": [],
            "notes": [],
        }
    delegates = {agent.name for agent in custom.agents}
    return {
        "error": None,
        "workflows": [
            _workflow(item) for item in sorted(custom.workflows, key=_by_name)
        ],
        "agents": [
            _agent(item, delegates) for item in sorted(custom.catalog, key=_by_name)
        ],
        "skills": [_skill(item) for item in sorted(custom.skills, key=_by_name)],
        "notes": [*custom.overrides, *custom.skipped],
    }

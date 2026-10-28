"""JSON view of every skill, agent, workflow, and hook in effect for a project."""

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


def _agent(item, delegates: set[str], claude_agents: set[str]) -> dict:
    return {
        "name": item.name,
        "role": item.role,
        "description": item.description,
        "scope": item.source.scope,
        "file": str(item.file),
        "ejected": item.ejected_from is not None,
        "delegate": item.name in delegates,
        "claude_resolvable": item.file.suffix == ".md" and item.name in claude_agents,
        "tools": None if item.tools is None else list(item.tools),
        "model": item.model,
        "skills": list(item.skills),
        "text": item.file.read_text(encoding="utf-8"),
    }


def _hook(item) -> dict:
    from meow.project.custom.hook_sync import syncable

    return {
        "name": item.name,
        "description": item.description,
        "scope": item.source.scope,
        "file": str(item.file),
        "event": item.event,
        "matcher": item.matcher,
        "command": item.command,
        "timeout": item.timeout,
        "roles": list(item.roles or item.source.roles),
        "synced": syncable(item),
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


_KINDS = ("workflows", "agents", "skills", "hooks")


def _merge(merged: dict, seen: dict, repo: str) -> None:
    catalog = build_catalog(Path(repo))
    if catalog["error"]:
        merged["notes"].append(f"{repo}: {catalog['error']}")
        return
    for kind in _KINDS:
        for item in catalog[kind]:
            key = item.get("file") or item["directory"]
            if key in seen[kind]:
                continue
            seen[kind].add(key)
            item["repo"] = repo if item["scope"] in {"project", "local"} else None
            merged[kind].append(item)
    merged["notes"].extend(n for n in catalog["notes"] if n not in merged["notes"])


def build_global_catalog(repos: list[str]) -> dict:
    merged = {
        "error": None,
        "workflows": [],
        "agents": [],
        "skills": [],
        "hooks": [],
        "notes": [],
    }
    seen = {kind: set() for kind in _KINDS}
    for repo in repos:
        _merge(merged, seen, repo)
    for kind in _KINDS:
        merged[kind].sort(key=lambda item: item["name"])
    return merged


def build_catalog(working_dir: Path) -> dict:
    try:
        custom = load_config(Path(working_dir))["customizations"]
    except (OSError, ValueError) as exc:
        return {
            "error": str(exc),
            "workflows": [],
            "agents": [],
            "skills": [],
            "hooks": [],
            "notes": [],
        }
    delegates = {agent.name for agent in custom.agents}
    from meow.ui.sessions import claude_agent_names

    claude_agents = claude_agent_names(Path(working_dir))
    return {
        "error": None,
        "workflows": [
            _workflow(item) for item in sorted(custom.workflows, key=_by_name)
        ],
        "agents": [
            _agent(item, delegates, claude_agents)
            for item in sorted(custom.catalog, key=_by_name)
        ],
        "skills": [_skill(item) for item in sorted(custom.skills, key=_by_name)],
        "hooks": [_hook(item) for item in sorted(custom.hooks, key=_by_name)],
        "notes": [*custom.overrides, *custom.skipped],
    }

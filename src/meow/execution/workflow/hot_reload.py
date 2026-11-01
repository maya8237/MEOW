"""Per-round reload of `[models]` and agent definitions; nothing else is hot."""

from pathlib import Path

from meow.infrastructure.logging import get_logger
from meow.project.config import load_config
from meow.project.custom.workflows import walk

logger = get_logger(__name__)
VERDICT_ROLES = ("tester", "reviewer")


def haiku_verdict_roles(sprint, steps) -> dict[str, str]:
    """Verdict roles (tester, reviewer, gate agents) resolving to a Haiku model."""
    if "models" not in sprint.config:
        return {}
    catalog = sprint.config.get("customizations")
    roles = {role: sprint.model(role) for role in VERDICT_ROLES}
    for step in walk(steps):
        if step.kind != "gate" or catalog is None:
            continue
        try:
            agent = catalog.agent(step.target)
        except (KeyError, ValueError):
            continue
        roles[agent.role] = sprint.model(agent.role, agent)
    return {
        role: model
        for role, model in roles.items()
        if model and "haiku" in model.lower()
    }


def warn_haiku_verdicts(sprint, steps, *, source: str) -> dict[str, str]:
    found = haiku_verdict_roles(sprint, steps)
    for role, model in sorted(found.items()):
        logger.warning(
            "haiku_verdict_role",
            role=role,
            model=model,
            source=source,
            hint="Haiku often skips the verdict format; use a stronger model.",
        )
    return found


def reload_config(state, steps) -> None:
    """Refresh models and agents from disk; keep the old config on any failure."""
    config = state.sprint.config
    root = config.get("_project_root")
    if not root or "models" not in config or "customizations" not in config:
        return
    try:
        fresh = load_config(Path(root))
    except Exception as exc:
        logger.warning("config_reload_failed", error=str(exc))
        return
    old_models, old_catalog = config["models"], config["customizations"]
    roles = sorted(
        role
        for role in {*old_models, *fresh["models"]}
        if old_models.get(role) != fresh["models"].get(role)
    )
    before = {agent.name: agent for agent in old_catalog.catalog}
    after = {agent.name: agent for agent in fresh["customizations"].catalog}
    agents = sorted(
        name for name in {*before, *after} if before.get(name) != after.get(name)
    )
    if not roles and not agents:
        return
    config["models"] = fresh["models"]
    config["customizations"] = fresh["customizations"]
    state.journal(
        "config_reloaded",
        results={
            "config_reloaded": {"round": state.round, "models": roles, "agents": agents}
        },
    )
    logger.info("config_reloaded", round=state.round, models=roles, agents=agents)
    warn_haiku_verdicts(state.sprint, steps, source="reload")

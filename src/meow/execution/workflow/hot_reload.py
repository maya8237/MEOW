"""Per-round reload of `[models]`; nothing else is hot."""

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


def _warn_agent_edits(old_catalog, fresh_catalog) -> None:
    if old_catalog is None:
        return
    before = {agent.name: agent for agent in old_catalog.catalog}
    after = {agent.name: agent for agent in fresh_catalog.catalog}
    changed = sorted(
        name for name in {*before, *after} if before.get(name) != after.get(name)
    )
    if changed:
        logger.warning(
            "agent_definitions_changed_not_applied",
            agents=changed,
            hint="Agent definition edits apply to the next run.",
        )


def reload_config(state, steps) -> None:
    """Refresh `[models]` from disk; keep the old config on any failure.

    Agent definitions are not hot: agents can write the worktree config, and
    a mid-run edit must not widen their own tools, delegates or hooks. A
    changed definition is logged and applies to the next run.
    """
    config = state.sprint.config
    root = config.get("_project_root")
    if not root or "models" not in config:
        return
    try:
        fresh = load_config(Path(root))
    except Exception as exc:
        logger.warning("config_reload_failed", error=str(exc))
        return
    _warn_agent_edits(config.get("customizations"), fresh["customizations"])
    old_models = config["models"]
    roles = sorted(
        role
        for role in {*old_models, *fresh["models"]}
        if old_models.get(role) != fresh["models"].get(role)
    )
    if not roles:
        return
    config["models"] = fresh["models"]
    state.journal(
        "config_reloaded",
        results={"config_reloaded": {"round": state.round, "models": roles}},
    )
    logger.info("config_reloaded", round=state.round, models=roles)
    warn_haiku_verdicts(state.sprint, steps, source="reload")

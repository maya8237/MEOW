"""Per-round reload of `[models]`; nothing else is hot."""

from pathlib import Path

from meow.infrastructure.logging import get_logger
from meow.project.config import load_config

logger = get_logger(__name__)


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


def reload_config(state) -> None:
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

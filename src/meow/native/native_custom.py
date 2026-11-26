"""`meow native custom`: the custom skills and agents in effect for a role."""

from collections.abc import Sequence
from pathlib import Path

from meow.native.native_models import resolve_native_model
from meow.project.config import config_root, load_config
from meow.project.custom import AGENT_ROLES, Customizations, CustomSource
from meow.project.custom.workflows import expand, fingerprint, select_workflow, walk


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


def stage(  # ruff: ignore[too-many-arguments] -- mirrors the `meow native stage` flags
    working_dir: Path,
    active_dir: Path,
    step_id: str,
    *,
    workflow_name: str,
    run_id: str,
    plan: Path | None = None,
    round_number: int = 1,
) -> dict:
    """The rendered prompt and report path of one agent or gate step, exactly as
    `meow run` would give it, for a workflow running in Claude Code."""
    from meow.execution.run_state import RunStore
    from meow.project.custom.rendering import render_agent
    from meow.project.custom.templates import render
    from meow.project.plan_files import plan_stage_file, stage_file
    from meow.project.prompts import lint_instructions, verdict_format

    config = load_config(config_root(working_dir, active_dir))
    catalog = config["customizations"]
    item = select_workflow(config, workflow_name)
    step = next((s for s in walk(expand(item, catalog)) if s.id == step_id), None)
    if step is None or step.kind not in {"agent", "gate"}:
        raise ValueError(
            f"workflow {item.name!r} has no agent or gate step {step_id!r}"
        )
    agent = catalog.agent(step.target)
    docs = active_dir / config["docs_dir"]
    output = plan_stage_file(plan, step.id) if plan else stage_file(docs, step.id)
    output.parent.mkdir(parents=True, exist_ok=True)
    record = RunStore(working_dir).load(run_id)
    values = {
        "active_dir": active_dir,
        "docs_dir": docs,
        "request": record.request,
        "feature_name": record.results.get("feature_name"),
        "plan_file": plan,
        "review_file": output,
        "report_file": output,
        "output_file": output,
        "round": round_number,
        "plan_given": plan is not None,
        "lint_instructions": lint_instructions(config["lint"]),
        "verdict_format": verdict_format(output, "finding")
        if step.kind == "gate"
        else None,
    }
    task = render(
        step.task
        or "Perform this stage for {{request}}. Write your report only to the "
        "exact path {{output_file}}.",
        values,
        where=str(agent.file),
    )
    return {
        "kind": step.kind,
        "blocks": step.blocks,
        "system_prompt": render_agent(agent, values),
        "task": task,
        "report_file": str(output),
        "tools": None if agent.tools is None else list(agent.tools),
        "model": agent.model,
    }


def _source(source: CustomSource) -> dict:
    return {
        "scope": source.scope,
        "directory": str(source.path),
        "config_file": str(source.config_file),
    }


def custom(  # ruff: ignore[too-many-arguments] -- shared host capability dispatch
    working_dir: Path,
    active_dir: Path,
    role: str | None = None,
    *,
    host: str = "claude",
    available_models: Sequence[str] | None = None,
) -> dict:
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
                **resolve_native_model(
                    agent.model, host=host, available_models=available_models
                ),
                "skills": list(agent.skills),
                "roles": list(agent.source.roles) or sorted(AGENT_ROLES),
                **_source(agent.source),
            }
            for agent in agents
        ],
        "overrides": list(found.overrides),
        "skipped": list(found.skipped),
    }

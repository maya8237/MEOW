"""Read-only upgrade warnings shared by catalog commands and run startup."""

import hashlib
import re
import sys

from meow.project.custom import sources
from meow.project.custom.workflows import BUILTIN_AGENTS, expand, walk

# Include conditional support paths when deciding whether an agent is unused.
# Keep the saved workflow fingerprint's existing dependency contract unchanged.
_POSSIBLE_AGENTS = BUILTIN_AGENTS | {
    "builtin.tasks": ("planner", "generator"),
    "builtin.deliver": ("lint-fixer", "review-fixer"),
}


def drift_message(item, kind):
    if not item.ejected_from:
        return None
    origin = re.fullmatch(
        r"builtin:(?=[^@]{1,64}@)([a-z0-9]+(?:-[a-z0-9]+)*)@([0-9a-f]{64})",
        item.ejected_from,
    )
    if origin is None:
        return f"Drift: {item.file}: invalid origin metadata; inspect ejected_from."
    name, saved_hash = origin.groups()
    shipped = sources.builtin_dir() / kind / f"{name}.toml"
    if not shipped.is_file():
        return (
            f"Drift: {item.file}: shipped {name} was removed. "
            "Keep this as a custom definition, or remove the copy if no longer needed."
        )
    current = hashlib.sha256(shipped.read_bytes()).hexdigest()
    return (
        (
            f"Drift: {item.file}: shipped {name} changed. "
            f"Compare your copy with {shipped}, then keep or update it deliberately."
        )
        if saved_hash != current
        else None
    )


def warn_definition(item, kind):
    if message := drift_message(item, kind):
        print(message, file=sys.stderr)


def _references(workflow, catalog):
    steps = tuple(walk(expand(workflow, catalog)))
    workflows = {workflow.name: workflow}
    pending = []
    for step in steps:
        if step.kind == "sequence":
            workflows[step.target] = catalog.workflow(step.target)
        if step.kind in {"agent", "gate"}:
            pending.append(step.target)
        pending.extend(_POSSIBLE_AGENTS.get(step.target, ()))
    used = set()
    while pending:
        name = pending.pop()
        if name in used:
            continue
        agent = catalog.agent(name)
        used.add(name)
        pending.extend(agent.agents)
        pending.extend(item.name for item in catalog.agents_for(agent.role))
    return workflows.values(), used


def workflow_diagnostics(workflow, catalog):
    try:
        workflows, used = _references(workflow, catalog)
    except (KeyError, ValueError):
        # Inspection must still work for a broken workflow. Validation owns errors;
        # an incomplete dependency graph cannot establish that an agent is unused.
        workflows, used = (workflow,), None
    messages = [
        message for item in workflows if (message := drift_message(item, "workflows"))
    ]
    for agent in catalog.catalog:
        if message := drift_message(agent, "agents"):
            messages.append(message)
        if agent.ejected_from and used is not None and agent.name not in used:
            messages.append(
                f"Unused: {agent.file}: agent {agent.name!r} is not used by "
                f"workflow {workflow.name!r}; another workflow may use it. "
                f"Inspect with meow workflows show {workflow.name}."
            )
    return messages


def warn_workflow(workflow, catalog):
    for message in workflow_diagnostics(workflow, catalog):
        print(message, file=sys.stderr)

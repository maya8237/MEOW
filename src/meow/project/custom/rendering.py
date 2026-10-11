"""Render an agent definition's prompt with the values one invocation has.

Every render sees every documented variable (`PROMPT_VARIABLES`), unset
ones as None, so an override can use any of them and a misspelt name still
fails loudly. `docs/WORKFLOWS.md` documents what each one holds.
"""

from collections.abc import Mapping
from functools import cache

from meow.project.custom.definitions import CustomAgent, load_agent
from meow.project.custom.sources import CustomSource, builtin_dir
from meow.project.custom.templates import render, template_names

PROMPT_VARIABLES = frozenset({
    # Run and project
    "active_dir",
    "docs_dir",
    "request",
    "feature_name",
    "bug_mode",
    "test",
    "check_worktree_hygiene",
    # Plan and verdict files
    "plan_file",
    "review_file",
    "report_file",
    "output_file",
    # Context computed by MEOW
    "shape",
    "preplan",
    "lint_instructions",
    "focus",
    # Review flows
    "review_basis",
    "has_diff",
    "request_label",
    "target",
    "branch",
    "source_sha",
    "target_sha",
    "merge_base",
    # Documentation update
    "baseline",
    "head",
    # Workflow state
    "round",
    "feedback",
    "steps",
    "inputs",
    # Code-owned contracts
    "verdict_format",
})


@cache
def builtin_agent(name: str) -> CustomAgent:
    """MEOW's shipped definition of `name`, ignoring every override."""
    source = CustomSource("agents", builtin_dir() / "agents", "builtin", None)
    return load_agent(builtin_dir() / "agents" / f"{name}.toml", source)


def builtin_role_agent(role: str) -> CustomAgent | None:
    """Resolve the shipped definition for a config role, when one exists."""
    name = role.replace("_", "-")
    if not (builtin_dir() / "agents" / f"{name}.toml").is_file():
        return None
    return builtin_agent(name)


def effective_models(config: dict) -> dict:
    """The models native mode would receive from SDK role lookups."""
    from meow.agents.base import ConfigLookups

    context = ConfigLookups()
    context.config = config
    return {
        role: context.model(role, builtin_role_agent(role)) for role in config["models"]
    }


def _template(agent: CustomAgent, variant: str | None) -> str:
    if variant is None:
        return agent.prompt
    if variant not in agent.variants:
        raise ValueError(
            f"{agent.file}: needs a [variants].{variant} prompt for this review"
        )
    return agent.variants[variant]


def render_agent(
    agent: CustomAgent,
    values: Mapping[str, object],
    *,
    variant: str | None = None,
) -> str:
    """The agent's prompt (or one of its variants) rendered with `values`."""
    unknown = set(values) - PROMPT_VARIABLES
    if unknown:
        raise ValueError(f"undocumented prompt variable(s): {sorted(unknown)}")
    variables = dict.fromkeys(PROMPT_VARIABLES)
    variables.update(values)
    return render(
        _template(agent, variant),
        variables,
        fragments=agent.fragments,
        where=str(agent.file),
    )


def unknown_variables(agent: CustomAgent) -> list[str]:
    """`file: name` for every name a prompt, variant, or fragment uses that
    no invocation provides."""
    problems = []
    for template in (agent.prompt, *agent.variants.values()):
        names = template_names(template, fragments=agent.fragments)
        problems.extend(
            f"{agent.file}: unknown variable {name!r}"
            for name in sorted(names)
            if name.split(".")[0] not in PROMPT_VARIABLES
        )
    return list(dict.fromkeys(problems))

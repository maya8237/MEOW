"""Render an agent's prompt for use outside a MEOW run.

An agent that opts in with `claude_agent` is meant to work as an ordinary
Claude Code subagent, where nothing supplies run values such as `plan_file`.
Standalone rendering gives every documented variable its unset value except
`active_dir`, so conditions on unset values are false and fragments are inlined.
A prompt that still prints a run-only value cannot work there. An agent's
`[variants] standalone` prompt, when present, replaces the main prompt.
"""

from meow.project.custom.definitions import CustomAgent
from meow.project.custom.rendering import PROMPT_VARIABLES
from meow.project.custom.templates import TemplateError, printed_names, render

VARIANT = "standalone"
VALUES = {"active_dir": "the current working directory"}


def _variables() -> dict[str, object]:
    return {**dict.fromkeys(PROMPT_VARIABLES), **VALUES}


def _template(agent: CustomAgent) -> str:
    return agent.variants.get(VARIANT, agent.prompt)


def standalone_problems(agent: CustomAgent) -> list[str]:
    """`file: ...` lines naming each run-only value the prompt still prints."""
    try:
        printed = printed_names(
            _template(agent),
            _variables(),
            fragments=agent.fragments,
            where=str(agent.file),
        )
    except TemplateError as exc:
        return [str(exc)]
    variables = _variables()
    blocked = sorted({
        name.split(".")[0]
        for name in printed
        if variables.get(name.split(".")[0]) is None
    })
    return [
        f"{agent.file}: {name!r} has no value outside a MEOW run; add a "
        f"[variants] {VARIANT} prompt without it, or drop claude_agent"
        for name in blocked
    ]


def render_standalone(agent: CustomAgent) -> str:
    """The prompt as Claude Code would send it; raises if it needs run values."""
    if problems := standalone_problems(agent):
        raise ValueError("\n".join(problems))
    return render(
        _template(agent),
        _variables(),
        fragments=agent.fragments,
        where=str(agent.file),
    )

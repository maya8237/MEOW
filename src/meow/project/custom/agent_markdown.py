"""Write an agent definition as a Claude Code Markdown file.

The body is the prompt template as written; frontmatter carries what `.md`
agents can express (see `agent_files.read_markdown_settings`). Variants and
fragments have no Markdown form, so an agent that uses either stays TOML.
"""

import json

from meow.project.custom.definitions import CustomAgent


def _settings(agent: CustomAgent, origin: str | None) -> list[str]:
    """Frontmatter lines for what the agent sets beyond its name and description."""
    optional = [
        (agent.tools is not None, f"tools: {', '.join(agent.tools or ())}"),
        (bool(agent.model), f"model: {agent.model}"),
        (bool(agent.skills), "skills:\n" + "\n".join(f"  - {n}" for n in agent.skills)),
        (bool(agent.agents), f"meow-agents: [{', '.join(agent.agents)}]"),
        (
            agent.claude_tools is not None,
            f"meow-claude-tools: {', '.join(agent.claude_tools or ())}",
        ),
        (agent.session != "one-shot", f"meow-session: {agent.session}"),
        (agent.lint_on_edit, "meow-lint-on-edit: true"),
        (
            agent.claude_agent is not None,
            f"meow-claude-agent: {str(agent.claude_agent).lower()}",
        ),
        (bool(origin), f"meow-ejected-from: {origin}"),
    ]
    return [line for wanted, line in optional if wanted]


def to_markdown(agent: CustomAgent, ejected_from: str | None = None) -> str:
    """The agent as a `.md` file; raises if it needs variants or fragments."""
    if agent.variants or agent.fragments:
        raise ValueError(
            f"agent {agent.name!r} uses variants or fragments, which only TOML "
            "can hold; eject it without --format md"
        )
    lines = [
        "---",
        f"name: {agent.name}",
        f"description: {json.dumps(agent.description, ensure_ascii=False)}",
        *_settings(agent, ejected_from or agent.ejected_from),
        "---",
        agent.prompt.strip(),
    ]
    return "\n".join(lines) + "\n"

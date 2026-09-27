"""Explorer agent definition and setup."""

from claude_agent_sdk import AgentDefinition


def make_explorer_agent(config: dict) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Read-only codebase/log/test-output exploration. Use for any "
            "research whose raw output doesn't need to be kept in full."
        ),
        prompt=(
            "You are a read-only research agent. Investigate the question "
            "you're given, then return only a concise summary with "
            "file:line references -- never dump raw file contents or full "
            "command output unless specifically asked to."
        ),
        tools=["Read", "Grep", "Glob", "Bash"],
        model=config["models"]["explorer"],
    )

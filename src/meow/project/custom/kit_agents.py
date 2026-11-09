"""Which kit agents may be synced to Claude Code, and when a change waits.

Claude Code can delegate to a synced agent on its own, so a kit agent's tools
matter more than its prose. Rules for a kit agent with `claude_agent`:

- It must declare `tools`; without them it would inherit every tool.
- In a project kit the reviewed `rev` bump is the approval.
- Elsewhere the first sync, and any change that adds a tool, wait until the
  user approves (`meow kits update NAME --accept`, or `auto_accept_hooks`).
  Approved tools are recorded in the kit's lock entry as `agents_ok`.

Narrowing tools and prose changes apply at once. Runs of MEOW itself are not
affected: `guard_tools` already bounds an agent there.
"""

from pathlib import Path

from meow.project.custom.definitions import CustomAgent, load_agent
from meow.project.custom.kits import lock_file, read_lock
from meow.project.custom.sources import CustomSource

LOCK_KEY = "agents_ok"


def kit_agent_tools(
    root: Path, only: tuple[str, ...] = ()
) -> dict[str, list[str] | None]:
    """Tools (None: undeclared) of each agent in a kit checkout that opts in."""
    folder = root / "agents"
    if (only and "agents" not in only) or not folder.is_dir():
        return {}
    source = CustomSource("agents", folder, "user", None, project_dir=root)
    found = {}
    for path in sorted(folder.iterdir()):
        if path.suffix not in {".md", ".toml"} or not path.is_file():
            continue
        agent = load_agent(path, source)
        if agent.claude_agent:
            tools = agent.claude_code_tools
            found[agent.name] = None if tools is None else list(tools)
    return found


def pending(
    tools: dict[str, list[str] | None], approved: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Agents whose first sync or added tools still need approval -> new tools."""
    return {
        name: sorted(set(wanted) - set(approved.get(name, ())))
        for name, wanted in tools.items()
        if wanted is not None
        and (name not in approved or set(wanted) - set(approved[name]))
    }


def approved_of(tools: dict[str, list[str] | None]) -> dict[str, list[str]]:
    return {name: list(wanted) for name, wanted in tools.items() if wanted is not None}


def hold_lines(kit: str, waiting: dict[str, list[str]]) -> list[str]:
    lines = [
        f"kit {kit}: agent '{name}' is on hold; "
        + (f"it would gain tools: {', '.join(added)}" if added else "it is new")
        for name, added in sorted(waiting.items())
    ]
    return [*lines, f"    approve with: meow kits update {kit} --accept"]


def _approved(
    agent: CustomAgent, project_dir: Path, user_dir: Path
) -> list[str] | None:
    source = agent.source
    entry = read_lock(lock_file(source.scope, project_dir, user_dir)).get(source.kit)
    return ((entry or {}).get(LOCK_KEY) or {}).get(agent.name)


def agent_hold(agent: CustomAgent, project_dir: Path, user_dir: Path) -> str | None:
    """Why a kit agent is not synced yet, or None when it may be."""
    source = agent.source
    if source.kit is None:
        return None
    if agent.claude_code_tools is None:
        return f"kit agent '{agent.name}': declare tools to sync it to Claude Code"
    waiting: dict[str, list[str]] = {}
    if source.scope != "project":
        approved = _approved(agent, project_dir, user_dir)
        waiting = pending(
            {agent.name: list(agent.claude_code_tools)},
            {} if approved is None else {agent.name: approved},
        )
    if not waiting:
        return None
    added = waiting[agent.name]
    why = f"would gain tools: {', '.join(added)}" if added else "is new"
    return (
        f"kit agent '{agent.name}' is on hold; it {why}; "
        f"approve with: meow kits update {source.kit} --accept"
    )

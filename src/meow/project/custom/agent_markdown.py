"""Read and write the body of a Claude Code Markdown agent definition.

The body is the prompt template; frontmatter carries the settings (see
`agent_files.read_markdown_settings`). Prompt variants and `{{> name}}`
fragments follow the prompt as sections, each opened by a marker line:

    <!-- meow:fragment NAME -->
    <!-- meow:variant NAME -->

A section runs to the next marker or the end of the file.
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meow.project.custom.definitions import CustomAgent

_MARKER = re.compile(
    r"^<!-- meow:(?P<kind>[a-z]+) (?P<name>[A-Za-z0-9_-]+) -->[ \t]*$", re.M
)
_KINDS = {"fragment": "fragments", "variant": "variants"}


def split_sections(body: str, path: Path) -> tuple[str, dict[str, str], dict[str, str]]:
    """The prompt, variants, and fragments of a Markdown agent body."""
    markers = list(_MARKER.finditer(body))
    found: dict[str, dict[str, str]] = {"variants": {}, "fragments": {}}
    for index, marker in enumerate(markers):
        kind, name = marker["kind"], marker["name"]
        if kind not in _KINDS:
            raise ValueError(f"{path}: unknown section kind {kind!r} in {marker[0]}")
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        text = body[marker.end() : end].strip()
        if not text:
            raise ValueError(f"{path}: {kind} {name!r} is empty")
        if name in found[_KINDS[kind]]:
            raise ValueError(f"{path}: {kind} {name!r} is defined twice")
        found[_KINDS[kind]][name] = text
    prompt = body[: markers[0].start()] if markers else body
    return prompt.strip(), found["variants"], found["fragments"]


def migrate_toml_agents(directories: list[Path]) -> list[str]:
    """Rewrite each retired `.toml` agent in `directories` as `.md`."""
    from meow.project.custom.agent_files import read_agent_toml
    from meow.project.custom.definitions import CustomAgent

    done = []
    for directory in directories:
        for path in (
            sorted(Path(directory).glob("*.toml")) if Path(directory).is_dir() else []
        ):
            target = path.with_suffix(".md")
            if target.exists():
                done.append(f"skipped {path}: {target.name} already exists")
                continue
            agent = CustomAgent(
                name=path.stem, file=path, source=None, **read_agent_toml(path)
            )
            target.write_text(to_markdown(agent), encoding="utf-8", newline="\n")
            path.unlink()
            done.append(f"migrated {path} -> {target.name}")
    return done


def _sections(agent: "CustomAgent") -> list[str]:
    return [
        f"\n<!-- meow:{kind} {name} -->\n{text.strip()}"
        for kind, table in (("fragment", agent.fragments), ("variant", agent.variants))
        for name, text in table.items()
    ]


def _settings(agent: "CustomAgent", origin: str | None) -> list[str]:
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


def to_markdown(agent: "CustomAgent", ejected_from: str | None = None) -> str:
    """The agent as a `.md` file, with any variants and fragments as sections."""
    lines = [
        "---",
        f"name: {agent.name}",
        f"description: {json.dumps(agent.description, ensure_ascii=False)}",
        *_settings(agent, ejected_from or agent.ejected_from),
        "---",
        agent.prompt.strip(),
        *_sections(agent),
    ]
    return "\n".join(lines) + "\n"

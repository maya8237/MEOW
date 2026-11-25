"""Read and write the body of a Claude Code Markdown agent definition.

The body is the prompt template; frontmatter carries the settings (see
`agent_files.read_markdown_settings`). Prompt variants and `{{> name}}`
fragments follow the prompt as sections, each opened by a marker line:

    <!-- meow:fragment NAME -->
    <!-- meow:variant NAME -->

A section runs to the next marker or the end of the file.
"""

import hashlib
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


_SAME = ("prompt", "variants", "fragments", "tools", "model", "skills", "agents")
_ORIGIN = re.compile(r"builtin:([a-z0-9]+(?:-[a-z0-9]+)*)@[0-9a-f]{64}")


def _content(agent: "CustomAgent") -> tuple:
    """What an agent does, ignoring whitespace around its texts."""
    return (
        agent.description,
        agent.prompt.strip(),
        {name: text.strip() for name, text in agent.variants.items()},
        {name: text.strip() for name, text in agent.fragments.items()},
        *(getattr(agent, key) for key in _SAME[3:]),
    )


def _restamped(agent: "CustomAgent") -> "CustomAgent":
    """An unmodified ejected copy points at the shipped `.md`, so the format
    change alone is not reported as drift; an edited copy keeps its origin."""
    from dataclasses import replace

    from meow.project.custom.rendering import builtin_agent

    origin = _ORIGIN.fullmatch(agent.ejected_from or "")
    if origin is None:
        return agent
    try:
        shipped = builtin_agent(origin[1])
    except (OSError, ValueError):
        return agent
    if _content(agent) != _content(shipped):
        return agent
    digest = hashlib.sha256(shipped.file.read_bytes()).hexdigest()
    return replace(agent, ejected_from=f"builtin:{origin[1]}@{digest}")


def migrate_toml_agents(directories: list[Path], apply: bool = True) -> list[str]:
    """Rewrite each retired `.toml` agent in `directories` as `.md`.

    The Markdown copy is written and checked before the TOML is removed; a
    file that cannot be read or converted is reported and left as it was.
    """
    from meow.project.custom.agent_files import read_agent_toml
    from meow.project.custom.definitions import CustomAgent

    done = []
    for directory in dict.fromkeys(Path(d) for d in directories):
        for path in sorted(directory.glob("*.toml")) if directory.is_dir() else []:
            target = path.with_suffix(".md")
            if target.exists():
                done.append(f"skipped {path}: {target.name} already exists")
                continue
            try:
                fields = read_agent_toml(path)
                agent = _restamped(
                    CustomAgent(name=path.stem, file=path, source=None, **fields)
                )
                text = to_markdown(agent)
            except (OSError, ValueError) as exc:
                done.append(f"skipped {path}: {exc}")
                continue
            done.append(
                f"{'migrated' if apply else 'will migrate'} {path} -> {target.name}"
            )
            if apply:
                target.write_text(text, encoding="utf-8", newline="\n")
                path.unlink()
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

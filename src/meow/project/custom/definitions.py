"""Load custom skills and agents from their configured directories."""

import re
from dataclasses import dataclass, replace
from pathlib import Path

from meow.project.custom import frontmatter
from meow.project.custom.sources import AGENT_ROLES, SCOPES, CustomSource

# The synthesized plugin every custom skill is served from; the SDK addresses
# its skills as `meow-custom:<name>`.
PLUGIN_NAME = "meow-custom"
RESERVED_AGENT_NAMES = frozenset({"explorer"})
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_MAX = 64


@dataclass(frozen=True)
class CustomSkill:
    name: str
    description: str
    directory: Path
    source: CustomSource

    @property
    def qualified_name(self) -> str:
        return f"{PLUGIN_NAME}:{self.name}"

    def applies_to(self, role: str) -> bool:
        return not self.source.roles or role in self.source.roles


@dataclass(frozen=True)
class CustomAgent:
    name: str
    description: str
    prompt: str
    file: Path
    source: CustomSource
    tools: tuple[str, ...] | None = None  # None: inherit the parent role's tools
    model: str | None = None
    skills: tuple[str, ...] = ()

    def applies_to(self, role: str) -> bool:
        return role in (self.source.roles or AGENT_ROLES)


@dataclass(frozen=True)
class Customizations:
    """Every custom skill and agent in effect, after scope precedence."""

    skills: tuple[CustomSkill, ...] = ()
    agents: tuple[CustomAgent, ...] = ()
    # "<kind> <name>: <scope> overrides <scope>" for each shadowed definition.
    overrides: tuple[str, ...] = ()

    def skills_for(self, role: str) -> list[str]:
        return [s.qualified_name for s in self.skills if s.applies_to(role)]

    def agents_for(self, role: str) -> list[CustomAgent]:
        return [agent for agent in self.agents if agent.applies_to(role)]


def _name(value: object, where: Path) -> str:
    if not isinstance(value, str) or not _NAME.match(value) or len(value) > _NAME_MAX:
        raise ValueError(
            f"{where}: name {value!r} must be lowercase letters, digits, and "
            f"single hyphens (at most {_NAME_MAX} characters)"
        )
    return value


def _description(data: dict, where: Path) -> str:
    value = data.get("description")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: frontmatter must set a 'description'")
    return value.strip()


def _names(value: object, key: str, where: Path) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",")]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{where}: {key!r} must be a list of names")
    return tuple(dict.fromkeys(value))


def _load_skill(directory: Path, source: CustomSource) -> CustomSkill:
    skill_file = directory / "SKILL.md"
    data, _ = frontmatter.parse(skill_file)
    name = _name(data.get("name", directory.name), skill_file)
    if name != directory.name:
        raise ValueError(
            f"{skill_file}: name {name!r} must match its directory {directory.name!r}"
        )
    return CustomSkill(name, _description(data, skill_file), directory, source)


def _load_agent(agent_file: Path, source: CustomSource) -> CustomAgent:
    data, body = frontmatter.parse(agent_file)
    name = _name(data.get("name", agent_file.stem), agent_file)
    if name in RESERVED_AGENT_NAMES:
        raise ValueError(f"{agent_file}: agent name {name!r} is reserved by MEOW")
    if not body:
        raise ValueError(f"{agent_file}: the body after the frontmatter is the prompt")
    tools = _names(data["tools"], "tools", agent_file) if "tools" in data else None
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError(f"{agent_file}: 'model' must be a model name")
    return CustomAgent(
        name=name,
        description=_description(data, agent_file),
        prompt=body,
        file=agent_file,
        source=source,
        tools=tools,
        model=model if model and model != "inherit" else None,
        skills=_names(data.get("skills", []), "skills", agent_file),
    )


def _definitions(source: CustomSource) -> list:
    if source.kind == "skills":
        return [
            _load_skill(child, source)
            for child in sorted(source.path.iterdir())
            if (child / "SKILL.md").is_file()
        ]
    return [_load_agent(child, source) for child in sorted(source.path.glob("*.md"))]


def _merge(kind: str, sources: list[CustomSource]) -> tuple[list, list[str]]:
    """Apply precedence: higher scopes win; a repeat within one scope errors."""
    chosen: dict[str, CustomSkill | CustomAgent] = {}
    overrides = []
    label = kind.removesuffix("s")
    ordered = sorted(
        (s for s in sources if s.kind == kind), key=lambda s: SCOPES.index(s.scope)
    )
    for source in ordered:
        for item in _definitions(source):
            previous = chosen.get(item.name)
            if previous is None:
                chosen[item.name] = item
            elif previous.source.scope == source.scope:
                raise ValueError(
                    f"custom {label} {item.name!r} is defined twice in "
                    f"{source.scope} scope: {previous.source.path} and {source.path}"
                )
            else:
                overrides.append(
                    f"{label} {item.name}: {previous.source.scope} "
                    f"overrides {source.scope}"
                )
    return list(chosen.values()), overrides


def discover(sources: list[CustomSource]) -> Customizations:
    """Read every configured directory and resolve scope precedence.

    An agent's `skills` entry naming a custom skill is qualified to the
    synthesized plugin; any other entry is passed through as installed.
    """
    if not sources:
        return Customizations()
    skills, skill_overrides = _merge("skills", sources)
    agents, agent_overrides = _merge("agents", sources)
    names = {skill.name for skill in skills}
    agents = [
        replace(
            agent,
            skills=tuple(
                f"{PLUGIN_NAME}:{name}" if name in names else name
                for name in agent.skills
            ),
        )
        for agent in agents
    ]
    return Customizations(
        tuple(skills), tuple(agents), tuple(skill_overrides + agent_overrides)
    )

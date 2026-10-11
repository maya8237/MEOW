"""Load skills and agents from every source and resolve scope precedence."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from meow.infrastructure.logging import get_logger
from meow.project.custom import frontmatter
from meow.project.custom.agent_files import read_agent_toml
from meow.project.custom.sources import AGENT_ROLES, SCOPES, CustomSource

# The synthesized plugin every custom skill is served from; the SDK addresses
# its skills as `meow-custom:<name>`.
PLUGIN_NAME = "meow-custom"
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_MAX = 64
logger = get_logger(__name__)


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
    agents: tuple[str, ...] = ()  # delegates, by agent name
    session: str = "one-shot"
    lint_on_edit: bool = False
    ejected_from: str | None = None
    variants: Mapping[str, str] = field(default_factory=dict, hash=False)
    fragments: Mapping[str, str] = field(default_factory=dict, hash=False)

    @property
    def role(self) -> str:
        """The name `[permissions]`, `[models]`, and the run journal use."""
        return self.name.replace("-", "_")

    def applies_to(self, role: str) -> bool:
        return role in (self.source.roles or AGENT_ROLES)


@dataclass(frozen=True)
class Customizations:
    """Every skill and agent in effect, after scope precedence."""

    skills: tuple[CustomSkill, ...] = ()
    # Agents offered to planner/generator as subagents: those from a
    # `[custom].agents` entry that do not replace a builtin definition.
    agents: tuple[CustomAgent, ...] = ()
    # "<kind> <name>: <scope> overrides <scope>" for each shadowed definition.
    overrides: tuple[str, ...] = ()
    # One sentence per configured user-scope directory that does not exist.
    skipped: tuple[str, ...] = ()
    # Every resolved agent definition, builtin ones included.
    catalog: tuple[CustomAgent, ...] = ()
    # Every source read, for workflow discovery.
    sources: tuple[CustomSource, ...] = ()

    def skills_for(self, role: str) -> list[str]:
        return [s.qualified_name for s in self.skills if s.applies_to(role)]

    def agents_for(self, role: str) -> list[CustomAgent]:
        return [agent for agent in self.agents if agent.applies_to(role)]

    def agent(self, name: str) -> CustomAgent:
        for agent in self.catalog:
            if agent.name == name:
                return agent
        known = ", ".join(sorted(agent.name for agent in self.catalog))
        raise KeyError(f"no agent named {name!r}; defined agents: {known}")


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


def _load_toml_agent(agent_file: Path, source: CustomSource) -> CustomAgent:
    name = _name(agent_file.stem, agent_file)
    return CustomAgent(
        name=name, file=agent_file, source=source, **read_agent_toml(agent_file)
    )


def load_agent(agent_file: Path, source: CustomSource) -> CustomAgent:
    if agent_file.suffix == ".toml":
        return _load_toml_agent(agent_file, source)
    data, body = frontmatter.parse(agent_file)
    name = _name(data.get("name", agent_file.stem), agent_file)
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
    if source.missing:
        return []
    if source.kind == "skills":
        return [
            _load_skill(child, source)
            for child in sorted(source.path.iterdir())
            if (child / "SKILL.md").is_file()
        ]
    files = sorted(
        child
        for child in source.path.iterdir()
        if child.suffix in {".md", ".toml"} and child.is_file()
    )
    return [load_agent(child, source) for child in files]


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


def _skipped(sources: list[CustomSource]) -> tuple[str, ...]:
    notes = []
    for source in sources:
        if not source.missing:
            continue
        notes.append(
            f"custom {source.kind} directory {source.path} does not exist "
            f"(user scope, from {source.config_file}); skipped"
        )
        logger.warning(
            "custom_source_missing",
            kind=source.kind,
            path=str(source.path),
            config_file=str(source.config_file),
        )
    return tuple(notes)


def _unique(sources: list[CustomSource]) -> list[CustomSource]:
    """One source per directory; a configured entry wins over the same
    directory found by convention, so its `roles` still apply."""
    chosen: dict[tuple[str, Path], CustomSource] = {}
    for source in sources:
        key = (source.kind, source.path.resolve())
        if key not in chosen or chosen[key].config_file is None:
            chosen[key] = source
    return list(chosen.values())


def discover(sources: list[CustomSource]) -> Customizations:
    """Read every source and resolve scope precedence.

    An agent's `skills` entry naming a custom skill is qualified to the
    synthesized plugin; any other entry is passed through as installed.
    """
    sources = _unique(sources)
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
    builtin_names = {
        agent.name
        for source in sources
        if source.kind == "agents" and source.scope == "builtin"
        for agent in _definitions(source)
    }
    delegates = [
        agent
        for agent in agents
        if agent.source.delegate and agent.name not in builtin_names
    ]
    return Customizations(
        tuple(skills),
        tuple(delegates),
        tuple(skill_overrides + agent_overrides),
        _skipped(sources),
        tuple(agents),
        tuple(sources),
    )

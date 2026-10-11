"""Skills, agents, and workflows, discovered from every scope.

Four scopes, highest priority first: local project (`.meow/config.local.toml`
and `.meow/local/`, just for this user), project (`.meow/config.toml` and
`.meow/`, versioned with the repo), user (`~/.meow/`, every project on this
computer), and builtin (MEOW's own definitions, shipped in `builtin/`). A
name defined in a higher scope replaces the same name from a lower one.
"""

from meow.project.custom.definitions import (
    PLUGIN_NAME,
    CustomAgent,
    Customizations,
    CustomSkill,
    discover,
)
from meow.project.custom.plugin import skill_plugin_dir
from meow.project.custom.sources import (
    AGENT_ROLES,
    SCOPES,
    SKILL_ROLES,
    CustomSource,
    builtin_dir,
    builtin_sources,
    conventional_dir,
    conventional_sources,
    parse_sources,
)

__all__ = [
    "AGENT_ROLES",
    "PLUGIN_NAME",
    "SCOPES",
    "SKILL_ROLES",
    "CustomAgent",
    "CustomSkill",
    "CustomSource",
    "Customizations",
    "builtin_dir",
    "builtin_sources",
    "conventional_dir",
    "conventional_sources",
    "discover",
    "parse_sources",
    "skill_plugin_dir",
]

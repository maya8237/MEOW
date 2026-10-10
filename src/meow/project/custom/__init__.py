"""Custom skills and agents discovered from configured directories.

Three scopes, highest priority first: local project (`.meow/config.local.toml`,
just for this user), project (`.meow/config.toml`, versioned with the repo),
and user (`~/.meow/config.toml`, every project on this computer). Each scope
lists directories in a `[custom]` table; a name defined in a higher scope
replaces the same name from a lower one.
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
    SKILL_ROLES,
    CustomSource,
    parse_sources,
)

__all__ = [
    "AGENT_ROLES",
    "PLUGIN_NAME",
    "SKILL_ROLES",
    "CustomAgent",
    "CustomSkill",
    "CustomSource",
    "Customizations",
    "discover",
    "parse_sources",
    "skill_plugin_dir",
]

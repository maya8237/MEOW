"""Filenames and lookup constants for MEOW's layered configuration."""

CONFIG_FILENAME = ".meow/config.toml"


LOCAL_CONFIG_FILENAME = ".meow/config.local.toml"


USER_CONFIG_FILENAME = ".meow/config.toml"

# Project definitions are shareable; other .meow paths hold local/runtime state.
SHARED_PROJECT_PATHS = (
    CONFIG_FILENAME,
    ".meow/skills",
    ".meow/plugin",
    ".meow/.claude-plugin",
    ".meow/agents",
    ".meow/workflows",
)

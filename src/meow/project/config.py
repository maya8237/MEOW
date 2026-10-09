"""
meow/config.py

Layered MEOW configuration loading and merging. The command models, schema
validation, environment expansion, and launch policy live in sibling
config_* and command_policy modules; this module re-exports them so existing
imports from meow.project.config keep working.
"""

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback -- pip install tomli

import platform
from pathlib import Path

from meow.project.command_policy import (
    _os_mismatch,
    _validate_os_compatibility,
    split_command,
)
from meow.project.config_env import (
    _expand_config_environment,
    unresolved_environment_references,
)
from meow.project.config_files import (
    CONFIG_FILENAME,
    LOCAL_CONFIG_FILENAME,
    USER_CONFIG_FILENAME,
)
from meow.project.config_models import (
    DEFAULT_FIX_FLAG,
    DevServerCommand,
    LintCommand,
    VerificationCommand,
    resolve_command_cwd,
)
from meow.project.config_schema import (
    LINT_ENTRY_KEYS,
    MCP_ENTRY_KEYS,
    SERVER_ENTRY_KEYS,
    TEST_ENTRY_KEYS,
    TESTER_KEYS,
    _normalize_lint_commands,
    _normalize_tester_config,
    _validate_max_rounds,
)

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_CONFIG",
    "DEFAULT_FIX_FLAG",
    "LINT_ENTRY_KEYS",
    "LOCAL_CONFIG_FILENAME",
    "MCP_ENTRY_KEYS",
    "SERVER_ENTRY_KEYS",
    "TESTER_KEYS",
    "TEST_ENTRY_KEYS",
    "USER_CONFIG_FILENAME",
    "DevServerCommand",
    "LintCommand",
    "VerificationCommand",
    "config_paths",
    "load_config",
    "resolve_command_cwd",
    "split_command",
    "unresolved_environment_references",
    "user_config_path",
]


DEFAULT_CONFIG = {
    "max_rounds": 8,
    "lint_timeout": 60,  # seconds before a per-file lint command is killed
    "docs_dir": ".meow/plans",
    "models": {
        "explorer": "haiku",
        "planner": None,  # None = engine default
        "generator": None,
        "reviewer": None,
        "issue_fetcher": None,
        "gitlab_fetcher": None,
        "github_fetcher": None,
        "lint_fixer": None,
        "review_fixer": None,
        "tester": None,
    },
    "delivery": {
        "target_branch": "dev",
        "gitlab": {"enabled": False},
    },
}


def _merge_config(base: dict, override: dict) -> dict:
    """Merge config layers, appending skill lists across every layer."""
    merged = dict(base)
    for key, value in override.items():
        if (
            key == "agent_skills"
            and isinstance(merged.get(key), dict)
            and isinstance(value, dict)
        ):
            skills = dict(merged[key])
            for role, additions in value.items():
                previous = skills.get(role)
                if isinstance(previous, list) and isinstance(additions, list):
                    skills[role] = [*previous, *additions]
                else:
                    skills[role] = additions
            merged[key] = skills
            continue
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def user_config_path() -> Path:
    """Return the optional per-user fallback config path."""
    return Path.home() / USER_CONFIG_FILENAME


def config_paths(working_dir: Path) -> tuple[Path, ...]:
    """Return configs from highest priority to lowest priority."""
    project = Path(working_dir)
    local = project / LOCAL_CONFIG_FILENAME
    shared = project / CONFIG_FILENAME
    paths = []
    if local.is_file():
        paths.append(local)
    if shared.is_file():
        paths.append(shared)
    fallback = user_config_path()
    if fallback.is_file() and fallback.resolve() not in {
        path.resolve() for path in paths
    }:
        paths.append(fallback)
    return tuple(paths)


def load_config(working_dir: Path) -> dict:
    from meow.infrastructure.checks import normalize_build
    from meow.infrastructure.worktree_setup import validate_setup
    from meow.project.permissions import parse_policy

    user_config = {}
    for config_path in reversed(config_paths(working_dir)):
        with open(config_path, "rb") as f:
            user_config = _merge_config(
                user_config, _expand_config_environment(tomllib.load(f))
            )

    config = {**DEFAULT_CONFIG, **user_config}
    config["models"] = {**DEFAULT_CONFIG["models"], **user_config.get("models", {})}
    lint_configured = "lint" in user_config
    config["lint"] = _normalize_lint_commands(user_config) if lint_configured else []
    _validate_os_compatibility(config["lint"])
    config["tester"] = _normalize_tester_config(user_config)
    config["build"] = normalize_build(user_config.get("build", []))
    config["permissions"] = parse_policy(user_config.get("permissions"))
    config["worktree_setup"] = validate_setup(user_config.get("worktree_setup"))
    delivery = user_config.get("delivery", {})
    if not isinstance(delivery, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [delivery] must be a table")
    target = delivery.get("target_branch", "dev")
    if not isinstance(target, str) or not target or target.startswith("-"):
        raise ValueError(
            f"{CONFIG_FILENAME}: [delivery].target_branch must be a branch name"
        )
    gitlab = delivery.get("gitlab", {})
    if not isinstance(gitlab, dict):
        raise ValueError(f"{CONFIG_FILENAME}: [delivery.gitlab] must be a table")
    config["delivery"] = {
        "target_branch": target,
        "gitlab": {"enabled": bool(gitlab.get("enabled", False))},
    }
    for table_name, commands in (
        ("[[tester.tests]]", config["tester"]["tests"]),
        ("[[tester.dev_server]]", config["tester"]["dev_server"]),
    ):
        for position, command in enumerate(commands, 1):
            problem = _os_mismatch(
                " ".join([command.command, *command.args]), platform.system()
            )
            if problem:
                raise ValueError(
                    f"{CONFIG_FILENAME}: {table_name} entry {position} "
                    f"command {command.command!r} {problem}"
                )
    _validate_max_rounds(config)

    return config

"""Layered MEOW configuration loading and merging."""

import platform
import tomllib
from pathlib import Path

from meow.project.command_policy import _os_mismatch, _validate_os_compatibility
from meow.project.config_env import _expand_config_environment
from meow.project.config_files import (
    CONFIG_FILENAME as _CONFIG_FILENAME,
)
from meow.project.config_files import (
    LOCAL_CONFIG_FILENAME as _LOCAL_CONFIG_FILENAME,
)
from meow.project.config_files import (
    USER_CONFIG_FILENAME as _USER_CONFIG_FILENAME,
)
from meow.project.config_schema import (
    _normalize_lint_commands,
    _normalize_tester_config,
    _validate_max_rounds,
)
from meow.project.custom import CustomSource, discover, parse_sources

__all__ = [
    "DEFAULT_CONFIG",
    "config_paths",
    "load_config",
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
    "delivery": {"target_branch": "dev"},
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
    return Path.home() / _USER_CONFIG_FILENAME


def config_paths(working_dir: Path) -> tuple[Path, ...]:
    """Return configs from highest priority to lowest priority."""
    project = Path(working_dir)
    local = project / _LOCAL_CONFIG_FILENAME
    shared = project / _CONFIG_FILENAME
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


def config_root(project_dir: Path, active_dir: Path | None = None) -> Path:
    """Directory config is read from for a run in `active_dir`.

    Normally the project root. A feature worktree that was just auto-onboarded
    holds the only `.meow/config.toml` until it is merged, so read it there.
    """
    project = Path(project_dir)
    if active_dir is None or (project / _CONFIG_FILENAME).is_file():
        return project
    active = Path(active_dir)
    return active if (active / _CONFIG_FILENAME).is_file() else project


def _layer_custom_sources(
    layer: dict, config_path: Path, working_dir: Path
) -> list[CustomSource]:
    """Pop and resolve one layer's `[custom]` table against its own scope."""
    project = Path(working_dir)
    resolved = config_path.resolve()
    if resolved == (project / _LOCAL_CONFIG_FILENAME).resolve():
        scope, base = "local", project
    elif resolved == (project / _CONFIG_FILENAME).resolve():
        scope, base = "project", project
    else:
        scope, base = "user", config_path.parent
    return parse_sources(layer.pop("custom", None), scope, base, config_path)


def load_config(working_dir: Path) -> dict:
    from meow.infrastructure.checks import normalize_build
    from meow.infrastructure.worktree_setup import validate_setup
    from meow.project.permissions import parse_policy

    user_config = {}
    custom_sources: list[CustomSource] = []
    for config_path in reversed(config_paths(working_dir)):
        with open(config_path, "rb") as f:
            layer = _expand_config_environment(tomllib.load(f))
        custom_sources.extend(_layer_custom_sources(layer, config_path, working_dir))
        user_config = _merge_config(user_config, layer)

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
        raise ValueError(f"{_CONFIG_FILENAME}: [delivery] must be a table")
    target = delivery.get("target_branch", "dev")
    if not isinstance(target, str) or not target or target.startswith("-"):
        raise ValueError(
            f"{_CONFIG_FILENAME}: [delivery].target_branch must be a branch name"
        )
    config["delivery"] = {"target_branch": target}
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
                    f"{_CONFIG_FILENAME}: {table_name} entry {position} "
                    f"command {command.command!r} {problem}"
                )
    _validate_max_rounds(config)
    config["customizations"] = discover(custom_sources)

    return config

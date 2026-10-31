"""Layered MEOW configuration loading and merging."""

import tomllib
from pathlib import Path

from meow.execution.run_failures import InvalidConfigError
from meow.infrastructure.worktree import default_target_branch
from meow.project.command_detection import command_defaults
from meow.project.command_policy import (
    _os_mismatch,
    _validate_os_compatibility,
    system_name,
)
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
    _normalize_orchestrator_config,
    _normalize_pipeline_config,
    _normalize_run_config,
    _normalize_tester_config,
    _validate_max_rounds,
    _validate_stall_rounds,
)
from meow.project.custom import (
    CustomSource,
    builtin_sources,
    conventional_sources,
    discover,
    parse_sources,
)
from meow.project.tester_setup import apply_browser_default

__all__ = [
    "DEFAULT_CONFIG",
    "config_paths",
    "load_config",
    "run_config",
    "user_config_path",
]


DEFAULT_CONFIG = {
    "max_rounds": 8,
    "stall_rounds": 2,  # identical failed rounds before a loop stops early; 0 = off
    "lint_timeout": 60,  # seconds before a per-file lint command is killed
    "docs_dir": ".meow/plans",
    "models": {
        "explorer": None,  # explorer.toml supplies "haiku"
        "planner": None,  # None = engine default
        "generator": None,
        "reviewer": None,
        "issue_fetcher": None,
        "gitlab_fetcher": None,
        "github_fetcher": None,
        "lint_fixer": None,
        "review_fixer": None,
        "tester": None,
        "decomposer": None,
        "triage": None,
        "integrator": None,
        "pull_request": None,
    },
    "delivery": {"target_branch": None},
    "workflow": {"default": "default"},
}


def _replace_mcp(key: str, previous: object, value: dict) -> bool:
    """References, inline launchers, and explicit empty tables replace definitions."""
    if key != "mcp":
        return False
    return (
        not value
        or "from" in value
        or ("command" in value and isinstance(previous, dict) and "from" in previous)
    )


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
        if (
            isinstance(merged.get(key), dict)
            and isinstance(value, dict)
            and not _replace_mcp(key, merged[key], value)
        ):
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
    if resolved == user_config_path().resolve():
        scope, base = "user", config_path.parent
    elif resolved == (project / _LOCAL_CONFIG_FILENAME).resolve():
        scope, base = "local", project
    elif resolved == (project / _CONFIG_FILENAME).resolve():
        scope, base = "project", project
    else:
        scope, base = "user", config_path.parent
    return parse_sources(layer.pop("custom", None), scope, base, config_path)


def _workflow_config(user_config):
    workflow = user_config.get("workflow", {})
    if not isinstance(workflow, dict) or set(workflow) - {"default"}:
        raise ValueError(f"{_CONFIG_FILENAME}: [workflow] accepts only 'default'")
    selection = workflow.get("default", "default")
    if not isinstance(selection, str) or not selection.strip():
        raise ValueError(f"{_CONFIG_FILENAME}: [workflow].default must be a name")
    return {"default": selection}


def _layered(working_dir: Path) -> tuple[dict, list[CustomSource]]:
    user_config: dict = {}
    custom_sources: list[CustomSource] = []
    for config_path in reversed(config_paths(working_dir)):
        with open(config_path, "rb") as f:
            layer = _expand_config_environment(tomllib.load(f))
        custom_sources.extend(_layer_custom_sources(layer, config_path, working_dir))
        user_config = _merge_config(user_config, layer)
    return user_config, custom_sources


def run_config(working_dir: Path) -> dict:
    """Only the validated `[run]` table, without loading definitions."""
    try:
        return _normalize_run_config(_layered(working_dir)[0])
    except ValueError as exc:
        raise InvalidConfigError(str(exc)) from exc


def load_config(working_dir: Path) -> dict:
    """Load layered config; any invalid value raises `InvalidConfigError`."""
    try:
        return _load_config(working_dir)
    except InvalidConfigError:
        raise
    except ValueError as exc:
        raise InvalidConfigError(str(exc)) from exc


def _load_config(working_dir: Path) -> dict:
    from meow.infrastructure.checks import normalize_build
    from meow.infrastructure.worktree_setup import install_timeout, validate_setup
    from meow.project.permissions import parse_policy

    user_config, custom_sources = _layered(working_dir)
    detected = command_defaults(Path(working_dir), user_config)
    user_config = _merge_config(detected.defaults, user_config)
    config = {**DEFAULT_CONFIG, **user_config}
    config["_detected_origins"] = dict(detected.origins)
    config["_project_root"] = str(Path(working_dir).resolve())
    config["models"] = {**DEFAULT_CONFIG["models"], **user_config.get("models", {})}
    lint_configured = "lint" in user_config
    config["lint"] = (
        _normalize_lint_commands(user_config)
        if lint_configured and user_config["lint"] != []
        else []
    )
    _validate_os_compatibility(config["lint"], system_name())
    config["tester"] = _normalize_tester_config(user_config)
    apply_browser_default(config)
    config["build"] = normalize_build(user_config.get("build", []))
    config["worktree_setup"] = validate_setup(user_config.get("worktree_setup"))
    config["install_timeout"] = install_timeout(user_config.get("worktree_setup"))
    delivery = user_config.get("delivery", {})
    if not isinstance(delivery, dict):
        raise ValueError(f"{_CONFIG_FILENAME}: [delivery] must be a table")
    target = delivery.get("target_branch")
    if target is None:
        target = default_target_branch(Path(working_dir))
    if not isinstance(target, str) or not target or target.startswith("-"):
        raise ValueError(
            f"{_CONFIG_FILENAME}: [delivery].target_branch must be a branch name"
        )
    config["delivery"] = {"target_branch": target}
    config["workflow"] = _workflow_config(user_config)
    config["orchestrator"] = _normalize_orchestrator_config(user_config)
    config["pipeline"] = _normalize_pipeline_config(user_config)
    config["run"] = _normalize_run_config(user_config)
    for table_name, commands in (
        ("[[tester.tests]]", config["tester"]["tests"]),
        ("[[tester.dev_server]]", config["tester"]["dev_server"]),
    ):
        for position, command in enumerate(commands, 1):
            problem = _os_mismatch(
                " ".join([command.command, *command.args]), system_name()
            )
            if problem:
                raise ValueError(
                    f"{_CONFIG_FILENAME}: {table_name} entry {position} "
                    f"command {command.command!r} {problem}"
                )
    _validate_max_rounds(config)
    _validate_stall_rounds(config)
    config["customizations"] = discover([
        *custom_sources,
        *conventional_sources(working_dir, user_config_path().parent),
        *builtin_sources(),
    ])
    config["permissions"] = parse_policy(
        user_config.get("permissions"),
        roles={agent.role for agent in config["customizations"].catalog},
    )
    from meow.project.custom.rendering import validate_agents

    validate_agents(config["customizations"])

    return config

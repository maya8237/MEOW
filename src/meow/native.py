"""
meow/native.py

The deterministic half of native (in-Claude-Code-session) execution.

When a meow skill runs inside a Claude Code session, that session plays
planner/generator and dispatches reviewer/explorer subagents itself -- no
Agent SDK process is involved. What the session cannot do reliably from
prose alone are the mechanical facts the Python harness already owns:
parsed `.harness.toml`, worktree and branch resolution, plan/review
lookup, lint execution, verdict parsing, the round counter, and the role
prompts. `native_cli.py` wires the functions re-exported here to `meow
native ...` and prints their results as JSON.

Each concern that used to live in this one file now has its own module:
`native_prepare.py` (worktree/clean-tree bootstrapping, directory
resolution, and plan/review lookup), `native_lint.py` (lint execution),
`native_state.py` (the on-disk round counter), and `native_prompt.py`
(prompt/agent-wiring construction). This module just re-exports their
public names, plus the one-line git-push wrapper, so `native_cli.py` and
other callers keep a single `from meow import native` import and a stable
`native.<name>` surface. Nothing here imports or starts the Agent SDK.
"""

import os
import shutil
from pathlib import Path

from meow.config import load_config, tomllib
from meow.native_lint import LintOptions, lint
from meow.native_prepare import (
    PrepareOptions,
    latest_plan,
    latest_review,
    prepare,
    verdict,
)
from meow.native_prompt import PROMPT_ROLES, role_prompt
from meow.native_state import round_state
from meow.worktree import _push_branch

__all__ = [
    "PROMPT_ROLES",
    "LintOptions",
    "PrepareOptions",
    "latest_plan",
    "latest_review",
    "lint",
    "prepare",
    "push",
    "role_prompt",
    "round_state",
    "verdict",
    "verify",
]


def push(active_dir: Path, branch: str) -> dict:
    _push_branch(active_dir, branch)
    return {"branch": branch, "pushed": True}


def verify(
    working_dir: Path,
    active_dir: Path | None = None,
    *,
    run_lint: bool = True,
) -> dict:
    """Validate all supported config sections and their local prerequisites."""
    config = load_config(working_dir)
    user_config = _read_user_config(working_dir)
    integrations = {
        "jira": _verify_jira(user_config),
        "gitlab": _verify_gitlab(user_config),
    }
    result = {
        "valid": True,
        "project_dir": str(working_dir.resolve()),
        "config_file": str((working_dir / ".harness.toml").resolve()),
        "docs_dir": config["docs_dir"],
        "max_rounds": config["max_rounds"],
        "lint_timeout": config["lint_timeout"],
        "lint": [
            {
                "command": command.command,
                "fix_flag": command.fix_flag,
                "per_file": command.per_file,
                "gate": command.gate,
            }
            for command in config["lint"]
        ],
        "models": config["models"],
        "integrations": integrations,
    }
    if run_lint:
        lint_result = lint(
            working_dir,
            active_dir or working_dir,
            LintOptions(all_blocking=False),
        )
        result["lint_result"] = lint_result
        result["lint_passed"] = bool(lint_result.get("clean", False))
    return result


def _read_user_config(working_dir: Path) -> dict:
    """Read raw .harness.toml tables after `load_config` validated it."""
    with (working_dir / ".harness.toml").open("rb") as config_file:
        return tomllib.load(config_file)


def _verify_mcp(
    name: str,
    mcp_config: object,
    *,
    required_env: tuple[str, ...] = (),
    config_env: dict | None = None,
) -> dict:
    """Report MCP config and local launch prerequisites without connecting."""
    if not isinstance(mcp_config, dict):
        return _not_configured()

    command, args = mcp_config.get("command"), mcp_config.get("args", [])
    env_config = config_env or {}
    if not isinstance(env_config, dict):
        env_config = {}
    environment = {**os.environ, **env_config}
    config_complete = isinstance(command, str) and bool(command.strip())
    command_available = bool(shutil.which(command)) if config_complete else False
    args_valid = isinstance(args, list) and all(
        isinstance(argument, str) for argument in args
    )
    missing_env = [key for key in required_env if not environment.get(key)]
    unresolved_env = [
        key for key, value in env_config.items()
        if isinstance(value, str) and value.startswith("$")
        and not environment.get(value[1:].strip("{}"))
    ]

    if not config_complete or not args_valid:
        status = "invalid_config"
    elif not command_available:
        status = "command_unavailable"
    elif missing_env or unresolved_env:
        status = "missing_environment"
    else:
        status = "ready_unchecked"

    return {
        "configured": True,
        "status": status,
        "command": command,
        "command_available": command_available,
        "args_valid": args_valid,
        "args_count": len(args) if isinstance(args, list) else None,
        "environment_keys": sorted(env_config),
        "required_environment_missing": missing_env,
        "referenced_environment_missing": sorted(set(unresolved_env)),
        "connection_tested": False,
        "connection_note": (
            "Configuration and local launch prerequisites were checked. "
            "Remote MCP authentication/connectivity requires an actual MCP "
            "tool call and was not tested by this read-only command."
        ),
    }


def _not_configured() -> dict:
    return {
        "configured": False,
        "status": "not_configured",
        "connection_tested": False,
    }


def _verify_jira(user_config: dict) -> dict:
    jira = user_config.get("jira")
    if not isinstance(jira, dict):
        return {
            "configured": False,
            "status": "not_configured",
            "mcp": _not_configured(),
        }
    mcp = _verify_mcp(
        "jira",
        jira.get("mcp"),
        required_env=("JIRA_URL",),
    )
    project_key_configured = bool(jira.get("project_key"))
    mcp["project_key_configured"] = project_key_configured
    if mcp["status"] == "ready_unchecked":
        if not project_key_configured:
            mcp["status"] = "missing_project_key"
        elif mcp["required_environment_missing"]:
            mcp["status"] = "missing_environment"
    return {
        "configured": bool(jira.get("mcp")),
        "status": mcp["status"],
        "project_key_configured": project_key_configured,
        "branch_prefix": jira.get("branch_prefix", "issue/"),
        "mcp": mcp,
    }


def _verify_gitlab(user_config: dict) -> dict:
    gitlab = user_config.get("gitlab")
    if not isinstance(gitlab, dict):
        return {
            "configured": False,
            "status": "not_configured",
            "mcp": _not_configured(),
        }
    mcp = gitlab.get("mcp")
    mcp_env = mcp.get("env", {}) if isinstance(mcp, dict) else {}
    check = _verify_mcp(
        "gitlab",
        mcp,
        required_env=("GITLAB_URL", "GITLAB_TOKEN"),
        config_env=mcp_env,
    )
    return {
        "configured": bool(mcp),
        "status": check["status"],
        "mcp": check,
    }

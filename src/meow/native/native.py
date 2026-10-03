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

from meow.infrastructure.worktree import _push_branch
from meow.integrations.knowledge import audit_project, select_findings, structural_check
from meow.integrations.knowledge_documents import (
    EvidenceDocumentWriter,
    create_selected_documents,
)
from meow.native.native_lint import LintOptions, lint
from meow.native.native_prepare import (
    PrepareOptions,
    latest_plan,
    latest_review,
    prepare,
    verdict,
)
from meow.native.native_prompt import PROMPT_ROLES, role_prompt
from meow.native.native_state import checkpoint, finalize, round_state
from meow.project.config import load_config, resolve_command_cwd, split_command, tomllib
from meow.project.shaping import (
    assess_request,
    load_shape_artifact,
    reflect_breadboard,
    save_shape_artifact,
)

__all__ = [
    "PROMPT_ROLES",
    "LintOptions",
    "PrepareOptions",
    "checkpoint",
    "finalize",
    "knowledge_audit",
    "knowledge_check",
    "knowledge_create",
    "latest_plan",
    "latest_review",
    "lint",
    "prepare",
    "push",
    "role_prompt",
    "round_state",
    "shape_assess",
    "shape_create",
    "shape_reflect",
    "verdict",
    "verify",
]


def knowledge_audit(root: Path) -> dict:
    return audit_project(root).to_dict()


def knowledge_check(root: Path) -> dict:
    return structural_check(root)


def knowledge_create(
    root: Path, finding_ids: list[str], *, overwrite: bool = False
) -> dict:
    audit = audit_project(root)
    findings = select_findings(audit, finding_ids)
    return {
        "audit": audit.to_dict(),
        "result": create_selected_documents(
            root, findings, writer=EvidenceDocumentWriter(), overwrite=overwrite
        ).__dict__,
    }


def shape_assess(request: str) -> dict:
    return assess_request(request).__dict__


def shape_create(path: Path, artifact: dict) -> dict:
    from meow.project.shaping import BreadboardArtifact, ShapeArtifact, ShapeOption

    value = (
        BreadboardArtifact(**artifact)
        if "places" in artifact
        else ShapeArtifact(
            artifact["problem"],
            tuple(artifact.get("constraints", ())),
            tuple(ShapeOption(**o) for o in artifact.get("options", ())),
            tuple(artifact.get("fit_checks", ())),
            artifact["chosen_approach"],
            tuple(artifact.get("assumptions", ())),
        )
    )
    save_shape_artifact(path, value)
    return {"path": str(path), "kind": type(value).__name__}


def shape_reflect(path: Path) -> dict:
    artifact = load_shape_artifact(path)
    return {
        "findings": list(reflect_breadboard(artifact))
        if hasattr(artifact, "places")
        else []
    }


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
        "tester": _verify_tester(config, active_dir or working_dir),
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


def _verify_tester(config: dict, active_dir: Path) -> dict:
    """Report tester config and local launch prerequisites without execution."""
    tester = config["tester"]
    tests = [_verify_command(item, active_dir) for item in tester["tests"]]
    servers = [_verify_command(item, active_dir) for item in tester["dev_server"]]
    mcp = []
    for entry in tester["mcp"]:
        command = entry["command"]
        unresolved = _unresolved_env(entry["env"])
        mcp.append({
            "name": entry["name"],
            "command": command,
            "args": entry["args"],
            "status": _launcher_status(command, unresolved),
            "unresolved_environment": unresolved,
        })

    architecture_paths = [
        active_dir / "docs" / "ARCHITECTURE.md",
        active_dir / "ARCHITECTURE.md",
        *(active_dir / path for path in tester["architecture_files"]),
    ]
    unique_paths = list(dict.fromkeys(path.resolve() for path in architecture_paths))
    architecture = []
    for path in unique_paths:
        exists = path.is_file()
        architecture.append({
            "path": str(path),
            "exists": exists,
            "readable": exists and os.access(path, os.R_OK),
        })
    return {
        "configured": bool(
            tests
            or servers
            or mcp
            or tester["test_dirs"]
            or tester["base_url"]
            or tester["architecture_files"]
        ),
        "tests": tests,
        "dev_servers": servers,
        "mcp": mcp,
        "test_dirs": [str(path) for path in tester["test_dirs"]],
        "test_directories": [
            {
                "path": str(path),
                "exists": (active_dir / path).is_dir(),
            }
            for path in tester["test_dirs"]
        ],
        "base_url": tester["base_url"],
        "architecture": architecture,
        "architecture_available": any(item["readable"] for item in architecture),
        "connection_tested": False,
    }


def _verify_command(command, active_dir: Path) -> dict:
    """Check command cwd and launcher without starting the command."""
    unresolved = _unresolved_env(command.env or {})
    try:
        cwd = resolve_command_cwd(active_dir, command.cwd)
        executable = split_command(command.command)[0]
        available = bool(shutil.which(executable))
        status = _launcher_status(executable, unresolved)
    except (OSError, ValueError) as exc:
        cwd = active_dir / command.cwd
        available = False
        status = "invalid_cwd"
        error = str(exc)
    else:
        error = None
    result = {
        "cwd": str(cwd),
        "command": command.command,
        "args": list(command.args),
        "launcher_available": available,
        "status": status,
        "unresolved_environment": unresolved,
    }
    if error:
        result["error"] = error
    if hasattr(command, "gate"):
        result["gate"] = command.gate
    return result


def _unresolved_env(environment: dict[str, str]) -> list[str]:
    """List missing variable names without returning configured values."""
    return [
        key
        for value in environment.values()
        if value.startswith("$")
        for key in [value[1:].strip("{}")]
        if not os.environ.get(key)
    ]


def _launcher_status(command: str, unresolved: list[str]) -> str:
    if not shutil.which(command):
        return "command_unavailable"
    return "missing_environment" if unresolved else "ready_unchecked"


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
        key
        for key, value in env_config.items()
        if isinstance(value, str)
        and value.startswith("$")
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

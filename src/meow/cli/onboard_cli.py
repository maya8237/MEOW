"""Explicit, agent-free project onboarding flags."""

import argparse
import json
import os
from pathlib import Path

from meow.project.config import load_config, user_config_path
from meow.project.custom import kit_ops
from meow.project.custom.kit_store import default_name, normalize_url
from meow.project.custom.registration import sync_registration
from meow.project.mcp_setup import (
    FAILURES,
    always_install,
    ask_to_install,
    parse_providers,
    prompt_value,
    setup_providers,
)
from meow.project.mcp_setup_config import load_specs
from meow.project.onboard_config import configure_project, parse_setting

_FIELDS = {
    "jira-project-key": "jira.project_key",
    "jira-url": "jira.mcp.env.JIRA_URL",
    "jira-username": "jira.mcp.env.JIRA_USERNAME",
    "jira-token": "jira.mcp.env.JIRA_API_TOKEN",
    "jira-personal-token": "jira.mcp.env.JIRA_PERSONAL_TOKEN",
    "gitlab-url": "gitlab.mcp.env.GITLAB_URL",
    "gitlab-token": "gitlab.mcp.env.GITLAB_TOKEN",
    "github-token": "github.mcp.env.GITHUB_PERSONAL_ACCESS_TOKEN",
    "target": "delivery.target_branch",
    "docs-dir": "docs_dir",
}


def add_onboard_parser(subparsers):
    parser = subparsers.add_parser(
        "onboard", help="Set up a project and explicitly selected optional features."
    )
    parser.add_argument("--work-dir", "-d", dest="working_dir", default=".")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt (this command is always agent-free and non-interactive).",
    )
    parser.add_argument(
        "--mcp",
        metavar="PROVIDERS",
        help=(
            "Register user-scope MCP servers (atlassian,gitlab or all). Credentials "
            "come from the servers' own environment variables, e.g. JIRA_URL and "
            "JIRA_API_TOKEN, never from flags."
        ),
    )
    parser.add_argument(
        "--install-missing",
        action="store_true",
        help="With --mcp, pip-install a missing server (Jira's mcp-atlassian).",
    )
    parser.add_argument(
        "--kit",
        metavar="URL",
        action="append",
        default=[],
        help=(
            "Install a kit (owner/repo, github:, gitlab:, or any git URL; "
            "repeatable). Its skills, agents, workflows, and hooks are listed "
            "in the JSON result."
        ),
    )
    parser.add_argument(
        "--kit-scope",
        choices=("project", "local", "user"),
        default="project",
        help="Where --kit is installed (default: project, shared via git).",
    )
    _add_settings(parser)
    _add_integrations(parser)


def _add_settings(parser):
    parser.add_argument(
        "--set",
        dest="settings",
        action="append",
        default=[],
        metavar="KEY=TOML",
        help="Set any optional config field locally; repeat for multiple fields.",
    )
    parser.add_argument(
        "--config", type=Path, help="Merge a TOML file into ignored local config."
    )
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--write-tests", action=argparse.BooleanOptionalAction)
    for kind in ("lint", "test", "build"):
        parser.add_argument(f"--{kind}-command", action="append")
    for flag in ("target", "docs-dir"):
        parser.add_argument(f"--{flag}")


def _add_integrations(parser):
    for provider in ("jira", "gitlab", "github"):
        group = parser.add_argument_group(f"{provider} (optional)")
        for flag in _FIELDS:
            if flag.startswith(f"{provider}-"):
                group.add_argument(f"--{flag}", help="Value or $ENV_VAR reference.")
        group.add_argument(f"--{provider}-command", help="MCP launcher executable.")
        group.add_argument(
            f"--{provider}-arg",
            action="append",
            help="MCP argument; repeat. For leading dashes: --jira-arg=--flag.",
        )


def _flag_settings(args):
    settings = []
    for flag, key in _FIELDS.items():
        value = getattr(args, flag.replace("-", "_"))
        if value is not None:
            settings.append((key, value))
    for key, value in (
        ("max_rounds", args.max_rounds),
        ("tester.write_tests", args.write_tests),
    ):
        if value is not None:
            settings.append((key, value))
    return settings


def _command_settings(args):
    settings = []
    for provider in ("jira", "gitlab", "github"):
        for suffix, field in (("command", "command"), ("arg", "args")):
            value = getattr(args, f"{provider}_{suffix}")
            if value is not None:
                settings.append((f"{provider}.mcp.{field}", value))
    for kind, key in (("lint", "lint"), ("test", "tester.tests"), ("build", "build")):
        commands = getattr(args, f"{kind}_command")
        if commands is not None:
            settings.append((key, [{"command": command} for command in commands]))
    return settings


_SITE_URLS = {
    "jira": "JIRA_URL",
    "confluence": "CONFLUENCE_URL",
    "gitlab": "GITLAB_API_URL",
}


def _site_value(provider: str, url: str) -> str:
    """GitLab's server wants the API root; the others take the site as is."""
    if provider != "gitlab" or url.rstrip("/").endswith("/api/v4"):
        return url
    return f"{url.rstrip('/')}/api/v4"


def _setup_environment(root: Path) -> dict[str, str]:
    """Process environment plus the project's non-secret site URLs as defaults."""
    environ = dict(os.environ)
    try:
        config = load_config(root)
    except (OSError, ValueError):
        return environ
    for provider, variable in _SITE_URLS.items():
        table = config.get(provider)
        url = table.get("url") if isinstance(table, dict) else None
        if isinstance(url, str) and url:
            environ.setdefault(variable, _site_value(provider, url))
    return environ


def _mcp_results(args, root: Path) -> tuple[list[dict], list[str]]:
    """Setup results and notes about config that was ignored."""
    try:
        specs, notes = load_specs(root)
    except ValueError as exc:
        return [{"provider": "mcp_setup", "status": "failed", "detail": str(exc)}], []
    prompt = None if args.non_interactive else prompt_value
    if args.install_missing:
        allow = always_install
    else:
        allow = None if args.non_interactive else ask_to_install
    results = setup_providers(
        parse_providers(args.mcp, specs),
        _setup_environment(root),
        specs=specs,
        prompt=prompt,
        allow_install=allow,
    )
    return [result.to_dict() for result in results], notes


def _kit_result(url: str, args, root: Path, user_dir: Path) -> dict:
    """Install one kit; failures and repeats are results, not crashes."""
    lines: list[str] = []
    name = default_name(normalize_url(url)) if _valid(url) else url
    scope = args.kit_scope
    if any(
        spec.name == name and spec.scope == scope
        for spec in kit_ops.collect_specs(root, user_dir)
    ):
        return {"url": url, "kit": name, "scope": scope, "status": "unchanged"}
    try:
        spec = kit_ops.install(root, user_dir, url, scope=scope, output=lines.append)
    except (OSError, ValueError) as exc:
        return {"url": url, "scope": scope, "status": "failed", "detail": str(exc)}
    return {
        "url": url,
        "kit": spec.name,
        "scope": scope,
        "status": "installed",
        "contents": [line.strip() for line in lines[1:]],
    }


def _valid(url: str) -> bool:
    try:
        normalize_url(url)
    except ValueError:
        return False
    return True


def _kit_results(args, root: Path) -> tuple[list[dict], list[str]]:
    """Results for each --kit, plus what syncing them with Claude Code changed."""
    user_dir = user_config_path().parent
    results = [_kit_result(url, args, root, user_dir) for url in args.kit]
    synced: list[str] = []
    if any(item["status"] == "installed" for item in results):
        try:
            custom = load_config(root)["customizations"]
            synced = sync_registration(custom, root, user_dir)
        except (OSError, ValueError) as exc:
            synced = [f"sync failed: {exc}"]
    return results, synced


def onboard_command(args, root: Path) -> int:
    try:
        settings = [*_flag_settings(args), *_command_settings(args)]
        settings.extend(parse_setting(value) for value in args.settings)
        result = configure_project(root, settings, config_file=args.config)
    except (OSError, ValueError):
        # Parser/schema errors can contain the supplied credentials or source text.
        print(
            "Onboarding failed: invalid settings, unreadable config, "
            "or unsafe project. "
            "Check flag types and existing TOML; no optional settings were saved."
        )
        return 1
    status = 0
    if args.mcp:
        result["mcp"], notes = _mcp_results(args, root)
        if notes:
            result["mcp_notes"] = notes
        status = int(any(item["status"] in FAILURES for item in result["mcp"]))
    if args.kit:
        result["kits"], synced = _kit_results(args, root)
        if synced:
            result["kits_synced"] = synced
        status = status or int(any(k["status"] == "failed" for k in result["kits"]))
    print(json.dumps(result, indent=2))
    return status

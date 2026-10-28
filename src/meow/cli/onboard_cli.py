"""Explicit, agent-free project onboarding flags."""

import argparse
import json
import os
from pathlib import Path

from meow.project.config import load_config
from meow.project.mcp_setup import (
    FAILURES,
    hidden_prompt,
    parse_providers,
    setup_providers,
)
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
            "Register user-scope MCP servers (jira,gitlab or all). Credentials "
            "come from the servers' own environment variables, e.g. JIRA_URL and "
            "JIRA_API_TOKEN, never from flags."
        ),
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


def _mcp_results(args, root: Path) -> list[dict]:
    prompt = None if args.non_interactive else hidden_prompt
    results = setup_providers(
        parse_providers(args.mcp), _setup_environment(root), prompt=prompt
    )
    return [result.to_dict() for result in results]


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
        result["mcp"] = _mcp_results(args, root)
        status = int(any(item["status"] in FAILURES for item in result["mcp"]))
    print(json.dumps(result, indent=2))
    return status

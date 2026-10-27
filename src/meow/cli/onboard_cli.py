"""Explicit, agent-free project onboarding flags."""

import argparse
import json
from pathlib import Path

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
    print(json.dumps(result, indent=2))
    return 0

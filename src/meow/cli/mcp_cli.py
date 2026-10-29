"""Explicit, agent-free user-scope MCP registration."""

import os
from pathlib import Path

from meow.project.mcp_setup import (
    FAILURES,
    always_install,
    describe,
    parse_providers,
    setup_providers,
)
from meow.project.mcp_setup_config import load_specs


def add_mcp_parser(subparsers):
    parser = subparsers.add_parser(
        "mcp", help="Register supported user-scope MCP servers."
    )
    parser.add_argument(
        "providers",
        metavar="PROVIDERS",
        help="Comma-separated providers (atlassian,gitlab or all).",
    )
    parser.add_argument(
        "--install-missing",
        action="store_true",
        help="Pip-install a missing server (Jira's mcp-atlassian).",
    )
    parser.add_argument(
        "--work-dir",
        "-d",
        dest="working_dir",
        default=".",
        help="Read project-local MCP setup overrides from this directory.",
    )


def mcp_command(args, root: Path) -> int:
    try:
        specs, notes = load_specs(root)
    except ValueError as exc:
        print(f"MCP setup: {exc}")
        return 1
    for note in notes:
        print(f"MCP setup: {note}")
    results = setup_providers(
        parse_providers(args.providers, specs),
        os.environ,
        specs=specs,
        allow_install=always_install if args.install_missing else None,
    )
    for result in results:
        print(describe(result))
    return int(any(result.status in FAILURES for result in results))

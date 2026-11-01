"""`meow sync`: register custom skills and hooks with Claude Code in one step."""

from pathlib import Path

from meow.cli.hooks_cli import add_sync_options
from meow.project.config import load_config, user_config_path
from meow.project.custom.registration import (
    auto_sync_message,
    check_registration,
    hook_output,
    sync_registration,
)
from meow.project.custom.sync import SYNC_SCOPES


def _auto(project_dir: Path) -> None:
    """Never fails: a broken config must not break Claude Code's startup."""
    try:
        custom = load_config(project_dir)["customizations"]
        message = auto_sync_message(custom, project_dir, user_config_path().parent)
    except Exception as exc:  # reported, never raised: startup must go on
        message = f"MEOW could not sync customizations: {exc}"
    output = hook_output(message)
    if output:
        print(output)


def sync_command(args, project_dir: Path) -> None:
    if getattr(args, "auto", False):
        _auto(project_dir)
        return
    custom = load_config(project_dir)["customizations"]
    user_dir = user_config_path().parent
    if args.check:
        problems = check_registration(custom, project_dir, user_dir)
        for problem in problems:
            print(problem)
        if problems:
            raise ValueError("Not everything is registered; run: meow sync")
        print("Custom skills and hooks are registered with Claude Code.")
        return
    scopes = SYNC_SCOPES if args.scope == "all" else (args.scope,)
    fixed = sync_registration(custom, project_dir, user_dir, scopes)
    for line in fixed:
        print(line)
    print(
        "Synced custom skills and hooks; restart Claude Code to load them."
        if fixed
        else "Custom skills and hooks were already registered."
    )


def add_sync_parser(subparsers):
    parser = subparsers.add_parser(
        "sync", help="Register custom skills and hooks with Claude Code."
    )
    add_sync_options(parser)
    parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            "Claude Code SessionStart mode: sync for this user only, print hook "
            "JSON, never fail."
        ),
    )

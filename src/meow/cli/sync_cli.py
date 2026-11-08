"""`meow sync`: register custom skills and hooks with Claude Code in one step."""

from pathlib import Path

from meow.cli.hooks_cli import add_sync_options
from meow.project.config import load_config, user_config_path
from meow.project.custom import kit_ops
from meow.project.custom.registration import (
    auto_sync_message,
    check_registration,
    hook_output,
    ignore_problems,
    old_layout,
    repair_gitignore,
    sync_registration,
)
from meow.project.custom.sync import SYNC_SCOPES


def _auto(project_dir: Path, event: str) -> None:
    """Never fails: a broken config must not break Claude Code's startup."""
    try:
        migrated = old_layout(project_dir, apply=True)
        if event == "SessionStart":  # the only event that may touch the network
            migrated += kit_ops.auto_update(project_dir, user_config_path().parent)
        custom = load_config(project_dir)["customizations"]
        message = auto_sync_message(
            custom, project_dir, user_config_path().parent, migrated
        )
        if event == "SessionStart":
            notice = kit_ops.pending_updates(project_dir, user_config_path().parent)
            message = "\n".join(part for part in (message, *notice) if part) or None
    except Exception as exc:  # reported, never raised: startup must go on
        message = f"MEOW could not sync customizations: {exc}"
    output = hook_output(message, event)
    if output:
        print(output)


def sync_command(args, project_dir: Path) -> None:
    if getattr(args, "auto", None):
        _auto(project_dir, args.auto)
        return
    migrated = [] if args.check else old_layout(project_dir, apply=True)
    custom = load_config(project_dir)["customizations"]
    user_dir = user_config_path().parent
    if args.check:
        problems = [
            *ignore_problems(project_dir),
            *check_registration(custom, project_dir, user_dir),
        ]
        for problem in problems:
            print(problem)
        if problems:
            raise ValueError("Not everything is registered; run: meow sync")
        print("Custom skills and hooks are registered with Claude Code.")
        return
    scopes = SYNC_SCOPES if args.scope == "all" else (args.scope,)
    fixed = [*migrated, *repair_gitignore(project_dir)]
    fixed += sync_registration(custom, project_dir, user_dir, scopes)
    fixed += repair_gitignore(project_dir)  # folders created just now
    for line in fixed:
        print(line)
    print(
        "Synced custom skills and hooks; run /reload-plugins if Claude Code "
        "does not list them yet."
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
        nargs="?",
        const="SessionStart",
        choices=("SessionStart", "PostToolUse"),
        help=(
            "Claude Code hook mode (default SessionStart): sync, print hook JSON, "
            "never fail."
        ),
    )

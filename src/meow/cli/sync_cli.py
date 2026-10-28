"""`meow sync`: register custom skills and hooks with Claude Code in one step."""

from pathlib import Path

from meow.cli.hooks_cli import add_sync_options
from meow.project.config import load_config, user_config_path
from meow.project.custom.hook_sync import check_hook_registration, sync_hook_plugins
from meow.project.custom.sync import (
    SYNC_SCOPES,
    check_skill_registration,
    sync_skill_plugins,
)


def sync_command(args, project_dir: Path) -> None:
    custom = load_config(project_dir)["customizations"]
    user_dir = user_config_path().parent
    if args.check:
        problems = [
            *check_skill_registration(custom.skills, project_dir, user_dir),
            *check_hook_registration(custom.hooks, project_dir, user_dir),
        ]
        for problem in problems:
            print(problem)
        if problems:
            raise ValueError("Not everything is registered; run: meow sync")
        print("Custom skills and hooks are registered with Claude Code.")
        return
    scopes = SYNC_SCOPES if args.scope == "all" else (args.scope,)
    fixed = [
        *sync_skill_plugins(custom.skills, project_dir, user_dir, scopes),
        *sync_hook_plugins(custom.hooks, project_dir, user_dir, scopes),
    ]
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

"""`meow hooks sync`: register hook definitions with Claude Code."""

from pathlib import Path

from meow.project.config import load_config, user_config_path
from meow.project.custom.hook_sync import check_hook_registration, sync_hook_plugins
from meow.project.custom.registration import old_layout
from meow.project.custom.sync import SYNC_SCOPES


def hooks_command(args, project_dir: Path) -> None:
    migrated = [] if args.check else old_layout(project_dir, apply=True)
    custom = load_config(project_dir)["customizations"]
    hooks = custom.hooks
    user_dir = user_config_path().parent
    if args.check:
        problems = [
            *old_layout(project_dir),
            *check_hook_registration(hooks, project_dir, user_dir),
        ]
        for problem in problems:
            print(problem)
        if problems:
            raise ValueError("Hooks are not all registered; run: meow hooks sync")
        print("Hooks are registered with Claude Code.")
        return
    scopes = SYNC_SCOPES if args.scope == "all" else (args.scope,)
    fixed = [
        *migrated,
        *sync_hook_plugins(hooks, project_dir, user_dir, scopes),
    ]
    for line in fixed:
        print(line)
    print(
        "Synced hooks; run /reload-plugins if Claude Code does not list them."
        if fixed
        else "Hooks were already registered."
    )


def add_sync_options(parser) -> None:
    """The `--scope`, `--check`, and `--work-dir` options every sync command takes."""
    parser.add_argument(
        "--scope", choices=("all", *SYNC_SCOPES), default="all", help="Scope to sync."
    )
    parser.add_argument(
        "--check", action="store_true", help="Report problems without changing files."
    )
    parser.add_argument("--work-dir", "-d", dest="working_dir", default=".")


def add_hooks_parser(subparsers):
    parser = subparsers.add_parser(
        "hooks", help="Register hook definitions with Claude Code."
    )
    actions = parser.add_subparsers(dest="hooks_action", required=True)
    sync = actions.add_parser("sync", help="Register hooks at every scope.")
    add_sync_options(sync)

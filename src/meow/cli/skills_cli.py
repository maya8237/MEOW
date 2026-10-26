"""`meow skills sync`: register custom skills with Claude Code."""

from pathlib import Path

from meow.project.config import load_config, user_config_path
from meow.project.custom.sync import (
    SYNC_SCOPES,
    check_skill_registration,
    sync_skill_plugins,
)


def skills_command(args, project_dir: Path) -> None:
    skills = load_config(project_dir)["customizations"].skills
    user_dir = user_config_path().parent
    if args.check:
        problems = check_skill_registration(skills, project_dir, user_dir)
        for problem in problems:
            print(problem)
        if problems:
            raise ValueError(
                "Custom skills are not all registered; run: meow skills sync"
            )
        print("Custom skills are registered with Claude Code.")
        return
    scopes = SYNC_SCOPES if args.scope == "all" else (args.scope,)
    fixed = sync_skill_plugins(skills, project_dir, user_dir, scopes)
    for line in fixed:
        print(line)
    print(
        "Synced custom skills; restart Claude Code to load them."
        if fixed
        else "Custom skills were already registered."
    )


def add_skills_parser(subparsers):
    parser = subparsers.add_parser(
        "skills", help="Register custom skills with Claude Code."
    )
    actions = parser.add_subparsers(dest="skills_action", required=True)
    sync = actions.add_parser("sync", help="Register custom skills at every scope.")
    sync.add_argument(
        "--scope", choices=("all", *SYNC_SCOPES), default="all", help="Scope to sync."
    )
    sync.add_argument(
        "--check", action="store_true", help="Report problems without changing files."
    )
    sync.add_argument("--work-dir", "-d", dest="working_dir", default=".")

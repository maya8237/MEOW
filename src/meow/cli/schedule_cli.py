"""CLI surface for durable scheduled runs."""

import argparse
import json


def add_schedule_parser(subparsers) -> None:
    parser = subparsers.add_parser("schedule", help="Schedule a future unattended run.")
    commands = parser.add_subparsers(dest="schedule_command", required=True)
    add = commands.add_parser(
        "add", help="Register a one-shot run with the OS scheduler."
    )
    add.add_argument(
        "--at", required=True, help="ISO timestamp or next HH:MM local time."
    )
    add.add_argument("--work-dir", "-d", dest="working_dir", default=".")
    add.add_argument(
        "run_argv", nargs=argparse.REMAINDER, help="-- run REQUEST --name NAME"
    )
    listing = commands.add_parser("list", help="List saved schedules and results.")
    listing.add_argument("--work-dir", "-d", dest="working_dir", default=".")
    cancel = commands.add_parser("cancel", help="Remove a schedule's OS trigger.")
    cancel.add_argument("schedule_id")
    cancel.add_argument("--work-dir", "-d", dest="working_dir", default=".")
    edit = commands.add_parser("edit", help="Change a pending schedule's run time.")
    edit.add_argument("schedule_id")
    edit.add_argument(
        "--at", required=True, help="ISO timestamp or next HH:MM local time."
    )
    edit.add_argument("--work-dir", "-d", dest="working_dir", default=".")


def schedule_command(args, repo) -> None:
    from meow.infrastructure.scheduling import (
        cancel_schedule,
        edit_schedule,
        list_schedules,
        schedule_run,
    )

    if args.schedule_command == "add":
        result = schedule_run(repo, args.at, args.run_argv)
    elif args.schedule_command == "cancel":
        result = cancel_schedule(repo, args.schedule_id)
    elif args.schedule_command == "edit":
        result = edit_schedule(repo, args.schedule_id, args.at)
    else:
        result = list_schedules(repo)
    print(json.dumps(result, ensure_ascii=False))

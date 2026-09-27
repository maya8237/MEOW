"""
meow/cli.py

The `meow` console-script entry point registered in pyproject.toml:
argument parsing and dispatch into meow.orchestrator.run_sprint.
"""

import argparse
import asyncio
import re
from pathlib import Path

from meow.orchestrator import run_sprint


def cli_main():
    parser = argparse.ArgumentParser(prog="meow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run a sprint for a feature request."
    )
    run_parser.add_argument("request", help="Feature request text.")
    run_parser.add_argument(
        "--project-root", default=".",
        help="Path to the project repo (default: current directory).",
    )

    args = parser.parse_args()

    if args.command == "run":
        project_root = Path(args.project_root).resolve()
        slug = re.sub(r"[^a-z0-9]+", "-", args.request.lower()).strip("-")[:50]
        asyncio.run(run_sprint(project_root, slug, args.request))


if __name__ == "__main__":
    cli_main()

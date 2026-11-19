"""`meow autopilot`: keep a native MEOW run, or any command, going until it succeeds.

meow autopilot "REQUEST" [--cap N] [-- CLAUDE_FLAGS...]
meow autopilot resume RUN_ID [--cap N] [-- CLAUDE_FLAGS...]
meow autopilot loop "PROMPT" [--until CMD] [--command CMD] [--cap N] [-- FLAGS...]
meow autopilot stop ID
meow autopilot status [ID]
"""

import argparse
import json
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from meow.infrastructure import autopilot_loop as loops
from meow.infrastructure.claude_flags import filter_flags
from meow.native import autopilot

SUBCOMMANDS = ("resume", "loop", "stop", "status", "hook", "_supervise")


def _split(argv: list[str]) -> tuple[list[str], list[str] | None]:
    """Arguments before `--`, and the Claude flags after it (None if absent)."""
    if "--" not in argv:
        return argv, None
    index = argv.index("--")
    return argv[:index], filter_flags(argv[index + 1 :])


def _parser(name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"meow autopilot {name}".strip())
    parser.add_argument("--work-dir", "-d", dest="working_dir", default=".")
    return parser


def _summary(state: loops.LoopState) -> dict:
    data = asdict(state)
    data["alive"] = loops.alive(state)
    return data


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def _started(state: loops.LoopState, kind: str) -> None:
    print(
        f"[autopilot] {kind} {state.id} started; it runs until success.\n"
        f"  status: meow autopilot status {state.id}\n"
        f"  stop:   meow autopilot stop {state.id}\n"
        f"  log:    {state.log}"
    )


def autopilot_command(argv: list[str]) -> int:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    """Dispatch `meow autopilot ...`; returns the exit code."""
    name = argv[0] if argv and argv[0] in SUBCOMMANDS else ""
    rest, flags = _split(argv[1:] if name else argv)
    parser = _parser(name)
    if name in {"", "resume", "loop"}:
        parser.add_argument(
            "--cap", type=int, default=None, help="Most launches (0: none)."
        )
    if name == "":
        parser.add_argument("request", nargs="+")
    elif name == "resume":
        parser.add_argument("run_id")
    elif name == "loop":
        parser.add_argument("prompt", nargs="?", default="")
        parser.add_argument("--until", help="Shell command; exit 0 means done.")
        parser.add_argument(
            "--command", help="Command template to run instead of Claude."
        )
    elif name in {"stop", "_supervise"}:
        parser.add_argument("id")
    elif name == "status":
        parser.add_argument("id", nargs="?")
    elif name == "hook":
        parser.add_argument("event")
    args = parser.parse_args(rest)
    repo = Path(args.working_dir).resolve()
    try:
        return _run(name, args, repo, flags)
    except ValueError as exc:
        print(f"autopilot: {exc}", file=sys.stderr)
        return 1


def _run(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements, too-many-return-statements]
    name: str, args, repo: Path, flags: list[str] | None
) -> int:
    if name == "":
        _started(
            autopilot.start_run(repo, " ".join(args.request), flags, args.cap), "run"
        )
        return 0
    if name == "resume":
        _started(autopilot.resume_run(repo, args.run_id, flags, args.cap), "run")
        return 0
    if name == "loop":
        if not args.prompt and not args.command:
            raise ValueError("give a prompt, --command, or both")
        command = shlex.split(args.command) if args.command else None
        state = autopilot.start_loop(
            repo,
            args.prompt,
            until=args.until,
            command=command,
            flags=flags,
            cap=args.cap,
        )
        _started(state, "loop")
        return 0
    if name == "stop":
        state = autopilot.stop(repo, args.id)
        print(
            f"[autopilot] {state.id} {'stopping' if loops.alive(state) else 'stopped'}."
        )
        return 0
    if name == "status":
        if args.id:
            _print(_summary(autopilot.resolve(repo, args.id)))
        else:
            _print([_summary(state) for state in loops.all_loops(repo)])
        return 0
    if name == "hook":
        try:
            payload = json.load(sys.stdin)
        except (ValueError, OSError):
            payload = {}
        if isinstance(payload, dict):
            result = autopilot.hook(repo, args.event, payload)
            if result.get("decision"):
                print(json.dumps(result))
        return 0
    autopilot.supervise(repo, args.id)  # _supervise
    return 0

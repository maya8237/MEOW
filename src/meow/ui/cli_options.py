"""Describe the real CLI options for structured controls."""

import argparse

from meow.cli.parser import _build_arg_parser
from meow.ui.sessions import _CLI_COMMANDS, _PROHIBITED_ARGUMENTS, _parser_for


def _controlled(action: argparse.Action, command: str) -> str | None:
    if set(action.option_strings) & _PROHIBITED_ARGUMENTS:
        return "Managed by the session server; unavailable as an advanced option."
    fields = {
        "feature_name": "Session name",
        "workflow": "Workflow",
        "input": "Workflow inputs",
        **(
            {"no_worktree": "Work in this repository directly"}
            if command in {"run", "plan"}
            else {}
        ),
    }
    if action.dest in fields:
        return f"Set using {fields[action.dest]} in the launcher."
    unavailable = {
        "help": "Help is shown here; this flag does not start a session.",
    }
    if command == "run":
        unavailable.update(
            dict.fromkeys(
                ("lint_fix", "report_only", "resume_at"),
                "UI feature runs start from a request and execute automatically in an "
                "isolated worktree. Use the CLI for this mode, or Resume a saved run.",
            )
        )
    return unavailable.get(action.dest)


def _option(action: argparse.Action, command: str) -> dict:
    numeric = (
        action.type is int or getattr(action.type, "__name__", "") == "_positive_int"
    )
    return {
        "flag": next(flag for flag in action.option_strings if flag.startswith("--")),
        "aliases": action.option_strings,
        "help": action.help if action.help != argparse.SUPPRESS else "",
        "kind": "switch" if action.nargs == 0 else "number" if numeric else "value",
        "nargs": action.nargs if action.nargs is not None else 1,
        "choices": list(action.choices) if action.choices is not None else None,
        "repeatable": isinstance(action, argparse._AppendAction),
        "controlled": _controlled(action, command),
    }


def cli_options() -> dict:
    parser = _build_arg_parser()
    return {
        "commands": {
            command: [
                _option(action, command)
                for action in _parser_for(parser, command)._actions
                if any(flag.startswith("--") for flag in action.option_strings)
                and action.help != argparse.SUPPRESS
            ]
            for command in sorted(_CLI_COMMANDS)
        }
    }

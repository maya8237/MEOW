"""Tab completion of MEOW commands and flags inside the IPython session."""

from __future__ import annotations

import argparse


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _descend(
    parser: argparse.ArgumentParser, words: list[str]
) -> argparse.ArgumentParser:
    for word in words:
        sub = _subparsers(parser)
        if sub is not None and word in sub.choices:
            parser = sub.choices[word]
    return parser


def _option_value_choices(
    parser: argparse.ArgumentParser, option: str
) -> list[str] | None:
    """Return choices for ``option``'s value, or None if it takes no value."""
    for action in parser._actions:
        if option in action.option_strings and action.nargs != 0:
            return [str(choice) for choice in action.choices or ()]
    return None


def _names(parser: argparse.ArgumentParser, current: str) -> list[str]:
    if current.startswith("-"):
        return [opt for action in parser._actions for opt in action.option_strings]
    sub = _subparsers(parser)
    if sub is None:
        return []
    return [action.dest for action in sub._choices_actions] or list(sub.choices)


def complete_words(
    parser: argparse.ArgumentParser, words: list[str], current: str
) -> list[str]:
    """Suggest completions for ``current`` given the completed ``words`` before it.

    ``words`` start at the subcommand (any ``meow`` prefix already stripped) and
    the parser is walked down through the subcommands they name.
    """
    parser = _descend(parser, words)
    value_choices = _option_value_choices(parser, words[-1]) if words else None
    candidates = _names(parser, current) if value_choices is None else value_choices
    return sorted({c for c in candidates if c.startswith(current)})


def register_completers(
    shell: object, parser: argparse.ArgumentParser, commands: tuple[str, ...]
) -> None:
    """Complete flags and subcommands for the bare and ``%`` magic forms."""

    def complete(_shell: object, event: object) -> list[str]:
        words = event.line.split()  # type: ignore[attr-defined]
        current = event.symbol  # type: ignore[attr-defined]
        if words and words[0].lstrip("%") == "meow":
            words = words[1:]
        else:
            words[0] = words[0].lstrip("%")
        if current and words and words[-1] == current:
            words = words[:-1]
        return complete_words(parser, words, current)

    for name in commands:
        shell.set_hook("complete_command", complete, str_key=name)  # type: ignore[attr-defined]
        shell.set_hook("complete_command", complete, str_key=f"%{name}")  # type: ignore[attr-defined]

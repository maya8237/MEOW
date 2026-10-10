"""Interactive IPython entry point for MEOW."""

from __future__ import annotations

import shlex
from collections.abc import Callable

# "run" is omitted: it would shadow IPython's built-in %run. Use `meow run`.
_COMMANDS = (
    "review",
    "plan",
    "status",
    "cancel",
    "worktree",
    "resume",
    "queue",
    "hooks",
    "native",
)


def _dispatch_line(line: str, dispatch: Callable[[list[str]], None]) -> None:
    dispatch(shlex.split(line))


def _build_magics() -> type:
    """Create the MEOW magics class, one named line magic per command."""
    from IPython.core.magic import Magics, line_magic, magics_class

    from meow.cli.cli import cli_main

    def make_magic(command: str, prefix: str) -> Callable[[object, str], None]:
        def magic(self: object, line: str) -> None:
            _dispatch_line(f"{prefix} {line}".strip(), cli_main)

        # line_magic records __name__ and looks it up on the class later.
        magic.__name__ = command
        return line_magic(command)(magic)

    namespace = {command: make_magic(command, command) for command in _COMMANDS}
    namespace["meow"] = make_magic("meow", "")
    return magics_class(type("MeowMagics", (Magics,), namespace))


def start_ipython() -> None:
    """Start IPython with MEOW commands available as bare magics."""
    try:
        from IPython import start_ipython as launch

        # Create the shell first: IPython's own Magics subclasses must exist
        # before ours, because the magic decorators queue methods in a global
        # registry that the next Magics subclass to be created claims.
        from IPython.terminal.interactiveshell import TerminalInteractiveShell
    except ImportError as exc:
        raise SystemExit(
            "IPython is required for interactive mode. "
            "Install it with `pip install meow[ipython]`."
        ) from exc

    from meow.cli.ipython_completion import register_completers
    from meow.cli.parser import _build_arg_parser

    shell = TerminalInteractiveShell.instance()
    shell.register_magics(_build_magics())
    register_completers(shell, _build_arg_parser(), (*_COMMANDS, "meow"))
    launch(argv=[])

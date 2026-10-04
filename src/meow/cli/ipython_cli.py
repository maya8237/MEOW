"""Interactive IPython entry point for MEOW."""

from __future__ import annotations

import shlex
from collections.abc import Callable

_COMMANDS = (
    "run",
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


def start_ipython() -> None:
    """Start IPython with MEOW commands available as bare magics."""
    try:
        from IPython import start_ipython as launch
        from IPython.core.magic import Magics, line_magic, magics_class
    except ImportError as exc:
        raise SystemExit(
            "IPython is required for interactive mode. "
            "Install it with `pip install meow[ipython]`."
        ) from exc

    from meow.cli.cli import cli_main

    @magics_class
    class MeowMagics(Magics):
        def _run(self, line: str, command: str) -> None:  # ruff: ignore[no-self-use]
            _dispatch_line(f"{command} {line}".strip(), _dispatch_cli)

        @line_magic("meow")
        def meow(self, line: str) -> None:  # ruff: ignore[no-self-use]
            _dispatch_line(line, _dispatch_cli)

    def _dispatch_cli(argv: list[str]) -> None:
        cli_main(argv)

    for command in _COMMANDS:
        setattr(
            MeowMagics,
            command,
            line_magic(command)(
                lambda self, line, command=command: self._run(line, command)
            ),
        )

    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    TerminalInteractiveShell.instance().register_magics(MeowMagics)
    launch(argv=[], user_ns={"meow": _dispatch_cli})

"""Directory prompt with Tab completion and history for the interactive installer."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

_ENTER = {"\r", "\n"}
_FUNCTION_KEY_PREFIX = {"\x00", "\xe0"}
_UP = "up"
_DOWN = "down"
_ARROWS = {"H": _UP, "P": _DOWN}

# Paths entered earlier in this run, oldest first (Up/Down recall them on Windows;
# readline keeps its own history elsewhere).
_history: list[str] = []


def path_matches(text: str) -> list[str]:
    """Return directories matching ``text``, each with a trailing separator."""
    expanded = os.path.expanduser(os.path.expandvars(text))
    directory, prefix = os.path.split(expanded)
    try:
        names = sorted(os.listdir(directory or "."))
    except OSError:
        return []
    return [
        os.path.join(directory, name) + os.sep
        for name in names
        if name.lower().startswith(prefix.lower())
        and os.path.isdir(os.path.join(directory, name))
    ]


def _readline_prompt() -> Callable[[str], str] | None:
    try:
        import readline
    except ImportError:
        return None

    def complete(text: str, state: int) -> str | None:
        matches = path_matches(text)
        return matches[state] if state < len(matches) else None

    readline.set_completer_delims("\n")
    readline.set_completer(complete)
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    return input


class _Line:
    """An editable line with Tab-completion candidates and history recall."""

    def __init__(self, history: list[str]) -> None:
        self.text = ""
        self.choices: list[str] = []
        self.cycle = 0
        self.history = history
        self.position = len(history)
        self.draft = ""

    def type(self, char: str) -> None:
        self.choices = []
        self.text += char

    def backspace(self) -> None:
        self.choices = []
        self.text = self.text[:-1]

    def complete(self) -> None:
        if not self.choices:
            self.choices = path_matches(self.text)
            self.cycle = 0
        if self.choices:
            self.text = self.choices[self.cycle % len(self.choices)]
            self.cycle += 1

    def previous(self) -> None:
        if self.position == 0:
            return
        if self.position == len(self.history):
            self.draft = self.text
        self.position -= 1
        self.choices = []
        self.text = self.history[self.position]

    def following(self) -> None:
        if self.position >= len(self.history):
            return
        self.position += 1
        self.choices = []
        at_end = self.position == len(self.history)
        self.text = self.draft if at_end else self.history[self.position]


def _apply_key(line: _Line, key: str) -> None:
    if key == "\b":
        line.backspace()
    elif key == "\t":
        line.complete()
    elif key == _UP:
        line.previous()
    elif key == _DOWN:
        line.following()
    elif len(key) == 1 and key.isprintable():
        line.type(key)


def _redraw(before: str, after: str) -> None:
    pad = max(0, len(before) - len(after))
    sys.stdout.write("\b" * len(before) + after + " " * pad + "\b" * pad)
    sys.stdout.flush()


def _read_key() -> str:
    import msvcrt

    char = msvcrt.getwch()
    if char in _FUNCTION_KEY_PREFIX:
        return _ARROWS.get(msvcrt.getwch(), "")
    return char


def _remember(text: str) -> None:
    if text and (not _history or _history[-1] != text):
        _history.append(text)


def _windows_prompt(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = _Line(_history)
    while True:
        key = _read_key()
        if key in _ENTER:
            sys.stdout.write("\n")
            _remember(line.text)
            return line.text
        if key == "\x03":
            raise KeyboardInterrupt
        before = line.text
        _apply_key(line, key)
        _redraw(before, line.text)


def path_prompt() -> Callable[[str], str]:
    """Return an ``input``-like function that completes directories on Tab.

    Falls back to plain ``input`` when there is no interactive terminal.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input
    if os.name == "nt":
        return _windows_prompt
    return _readline_prompt() or input

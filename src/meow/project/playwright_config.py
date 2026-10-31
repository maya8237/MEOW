"""Literal-only reader for ``playwright.config.*``. Never evaluates or imports."""

import re
from dataclasses import dataclass
from pathlib import Path

from meow.project.command_detection import PLAYWRIGHT_CONFIG_NAMES, _read

_NAMES = PLAYWRIGHT_CONFIG_NAMES
_NUMBER = re.compile(r"[0-9][0-9_]*(?:\.[0-9_]+)?")
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_OPENERS, _CLOSERS = "{[", "}]"
_ENDS = {("punct", ","), ("punct", "}"), ("punct", "]")}
_DYNAMIC = object()


@dataclass(frozen=True)
class ServerEntry:
    command: str
    url: str | None
    port: int | None
    cwd: str
    timeout: float | None


@dataclass(frozen=True)
class PlaywrightConfig:
    file: str | None = None
    servers: tuple[ServerEntry, ...] = ()
    base_url: str | None = None
    channels: frozenset[str] = frozenset()
    executable: str | None = None
    unreadable: tuple[str, ...] = ()


def _skip_trivia(text: str, i: int) -> int:
    """Index of the next token start, past whitespace and comments."""
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = len(text) if i < 0 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i)
            i = len(text) if i < 0 else i + 2
        else:
            break
    return i


def _quoted(text: str, i: int) -> tuple[tuple[str, object], int]:
    quote, value, j = text[i], [], i + 1
    while j < len(text) and text[j] != quote:
        if text[j] == "\\" and j + 1 < len(text):
            j += 1
        value.append(text[j])
        j += 1
    return ("str", "".join(value)), j + 1


def _template(text: str, i: int) -> tuple[tuple[str, object], int]:
    """A backtick string; one containing ``${...}`` is dynamic."""
    dynamic, depth, j = False, 0, i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text.startswith("${", j):
            dynamic, depth, j = True, depth + 1, j + 2
        elif depth and text[j] == "}":
            depth, j = depth - 1, j + 1
        elif not depth and text[j] == "`":
            return (("dynamic", None) if dynamic else ("str", text[i + 1 : j])), j + 1
        else:
            j += 1
    return ("dynamic", None), j


def _token(text: str, i: int) -> tuple[tuple[str, object], int]:
    if text[i] in "'\"":
        return _quoted(text, i)
    if text[i] == "`":
        return _template(text, i)
    for kind, pattern in (("num", _NUMBER), ("ident", _IDENT)):
        match = pattern.match(text, i) if kind == "ident" or text[i].isdigit() else None
        if match:
            return (kind, match.group()), match.end()
    return ("punct", text[i]), i + 1


def _tokenize(text: str) -> list[tuple[str, object]]:
    tokens, i = [], _skip_trivia(text, 0)
    while i < len(text):
        token, i = _token(text, i)
        tokens.append(token)
        i = _skip_trivia(text, i)
    return tokens


def _literal(token: tuple[str, object]):
    kind, value = token
    if kind == "str":
        return value
    if kind == "num":
        text = str(value).replace("_", "")
        return float(text) if "." in text else int(text)
    return _DYNAMIC


class _Reader:
    def __init__(self, tokens: list, file: str):
        self.tokens, self.file, self.problems = tokens, file, []

    def _group_end(self, start: int) -> int | None:
        depth = 0
        for index in range(start, len(self.tokens)):
            kind, value = self.tokens[index]
            if kind == "punct" and value in _OPENERS:
                depth += 1
            elif kind == "punct" and value in _CLOSERS:
                depth -= 1
                if depth == 0:
                    return index
        return None

    def _value(self, index: int):
        """A lone string/number literal at ``index``; anything else is dynamic."""
        if index >= len(self.tokens):
            return _DYNAMIC
        after = (
            self.tokens[index + 1] if index + 1 < len(self.tokens) else ("punct", "}")
        )
        return _literal(self.tokens[index]) if after in _ENDS else _DYNAMIC

    def _keys(self, key: str) -> list[int]:
        """Value indexes of every ``key:`` in the file."""
        return [
            i + 2
            for i in range(len(self.tokens) - 2)
            if self.tokens[i][0] in {"ident", "str"}
            and self.tokens[i][1] == key
            and self.tokens[i + 1] == ("punct", ":")
        ]

    def _properties(self, start: int, end: int) -> dict[str, int]:
        """Top-level ``key: value`` pairs of the object at tokens[start..end]."""
        found, depth = {}, 0
        for i in range(start + 1, end):
            kind, value = self.tokens[i]
            if kind == "punct":
                depth += (value in _OPENERS) - (value in _CLOSERS)
            elif (
                depth == 0
                and kind in {"ident", "str"}
                and self.tokens[i + 1] == ("punct", ":")
            ):
                found.setdefault(str(value), i + 2)
        return found

    def _members(self, start: int, end: int) -> list[int]:
        """Start indexes of the objects directly inside the array at ``start``."""
        depth, found = 0, []
        for i in range(start, end):
            kind, value = self.tokens[i]
            if kind != "punct":
                continue
            if value == "{" and depth == 1:
                found.append(i)
            depth += (value in _OPENERS) - (value in _CLOSERS)
        return found

    def _literal(self, props: dict[str, int], key: str, kinds):
        if key not in props:
            return None
        value = self._value(props[key])
        if value is _DYNAMIC or not isinstance(value, kinds):
            self.problems.append(f"webServer.{key} in {self.file}")
            return None
        return value

    def server(self, start: int) -> ServerEntry | None:
        end = self._group_end(start)
        if end is None:
            self.problems.append(f"webServer in {self.file}")
            return None
        props = self._properties(start, end)
        command = self._literal(props, "command", str)
        url = self._literal(props, "url", str)
        port = self._literal(props, "port", int)
        cwd = self._literal(props, "cwd", str)
        timeout = self._literal(props, "timeout", (int, float))
        if command is None or (url is None and port is None):
            return None
        return ServerEntry(
            command, url, port, cwd or ".", None if timeout is None else timeout / 1000
        )

    def servers(self) -> tuple[ServerEntry, ...]:
        entries = []
        for index in self._keys("webServer"):
            end = self._group_end(index) if index < len(self.tokens) else None
            if end is None:
                self.problems.append(f"webServer in {self.file}")
                continue
            objects = (
                self._members(index, end) if self.tokens[index][1] == "[" else [index]
            )
            entries.extend(filter(None, map(self.server, objects)))
        return tuple(entries)

    def collect(self, key: str) -> set[str]:
        values = set()
        for index in self._keys(key):
            value = self._value(index)
            if isinstance(value, str):
                values.add(value)
            else:
                self.problems.append(f"{key} in {self.file}")
        return values

    def single(self, key: str) -> str | None:
        values = self.collect(key)
        if len(values) > 1:
            self.problems.append(f"{key} conflicts in {self.file}")
        return next(iter(values)) if len(values) == 1 else None


def read_playwright_config(root: Path) -> PlaywrightConfig:
    for name in _NAMES:
        text = _read(root, name)
        if text is None:
            continue
        reader = _Reader(_tokenize(text), name)
        servers = reader.servers()
        base_url = reader.single("baseURL")
        channels = frozenset(reader.collect("channel"))
        executable = reader.single("executablePath")
        return PlaywrightConfig(
            name,
            servers,
            base_url,
            channels,
            executable,
            tuple(dict.fromkeys(reader.problems)),
        )
    return PlaywrightConfig()

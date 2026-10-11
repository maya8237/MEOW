"""The strict template language of agent prompts and workflow tasks.

- `{{name}}` / `{{name.field}}` substitutes a value (None renders empty).
- `{{#if expr}}...{{else}}...{{/if}}` and `{{#unless expr}}...{{/unless}}`
  branch on an expression (see `expressions`).
- `{{> fragment}}` includes a fragment rendered with the same values.

Anything unknown -- a name, a fragment, an unclosed block -- raises
TemplateError naming `where`, so a broken override fails before an agent
starts. Template text is folded like prose (see `fold`); substituted values
are inserted after folding and never reflowed.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from meow.project.custom.expressions import ExpressionError, evaluate, names, parse

_TAG = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)
# A block tag alone on its line takes its line break with it.
_STANDALONE = re.compile(
    r"^[ \t]*(\{\{\s*(?:[#/][^}]*|else)\s*\}\})[ \t]*\r?\n", re.MULTILINE
)
_PARAGRAPH_BREAK = re.compile(r"(?:[ \t]*\n){3,}")
_STRUCTURAL = re.compile(r"^(?:\s|[-*] |#|\d+\. )")
_BLOCKS = {"if", "unless"}
# Values are parked in private-use characters while template text is folded.
_SLOT_OPEN, _SLOT_CLOSE = chr(0xE000), chr(0xE001)
_SLOT_PATTERN = re.compile(re.escape(_SLOT_OPEN) + r"(\d+)" + re.escape(_SLOT_CLOSE))


class TemplateError(ValueError):
    """A template does not parse or does not match the values it is given."""


@dataclass
class _Node:
    kind: str  # text, var, include, if, unless
    value: str = ""
    body: list["_Node"] | None = None
    other: list["_Node"] | None = None


def _error(where: str, message: str) -> TemplateError:
    return TemplateError(f"{where}: {message}" if where else message)


def _parse(text: str, where: str) -> list[_Node]:
    root: list[_Node] = []
    stack: list[tuple[_Node, list[_Node]]] = []
    current = root
    position = 0
    text = _STANDALONE.sub(r"\1", text)
    for match in _TAG.finditer(text):
        if match.start() > position:
            current.append(_Node("text", text[position : match.start()]))
        position = match.end()
        current = _tag(match.group(1), current, stack, where)
    if position < len(text):
        current.append(_Node("text", text[position:]))
    if stack:
        raise _error(where, f"unclosed {{{{#{stack[-1][0].kind}}}}} block")
    return root


_Stack = list[tuple[_Node, list[_Node]]]


def _open_block(tag: str, current: list[_Node], stack: _Stack, where: str):
    head, _, rest = tag.partition(" ")
    node = _Node(head[1:], rest.strip(), body=[], other=[])
    _check_expression(node.value, where)
    current.append(node)
    stack.append((node, current))
    return node.body


def _else(current: list[_Node], stack: _Stack, where: str) -> list[_Node]:
    other = stack[-1][0].other if stack else None
    if other is None or current is other:
        raise _error(where, "{{else}} outside an {{#if}} block")
    return other


def _close_block(head: str, stack: _Stack, where: str) -> list[_Node]:
    if not stack or stack[-1][0].kind != head[1:]:
        raise _error(where, f"{{{{{head}}}}} does not close an open block")
    return stack.pop()[1]


def _tag(tag: str, current: list[_Node], stack: _Stack, where: str) -> list[_Node]:
    """Apply one tag to the tree; return the list the next node goes into."""
    head, _, rest = tag.partition(" ")
    if head.startswith("#") and head[1:] in _BLOCKS:
        return _open_block(tag, current, stack, where)
    if head == "else":
        return _else(current, stack, where)
    if head.startswith("/"):
        return _close_block(head, stack, where)
    if head == ">":
        current.append(_Node("include", rest.strip()))
    else:
        _check_expression(tag, where)
        current.append(_Node("var", tag))
    return current


def _check_expression(text: str, where: str) -> None:
    try:
        parse(text)
    except ExpressionError as exc:
        raise _error(where, str(exc)) from exc


class _Renderer:
    def __init__(
        self, variables: Mapping[str, object], fragments: Mapping[str, str], where: str
    ):
        self.variables = variables
        self.fragments = fragments
        self.where = where
        self.values: list[str] = []
        self.including: list[str] = []

    def _evaluate(self, text: str) -> object:
        try:
            return evaluate(text, self.variables)
        except ExpressionError as exc:
            raise _error(self.where, str(exc)) from exc

    def nodes(self, nodes: list[_Node]) -> str:
        return "".join(self.node(node) for node in nodes)

    def node(self, node: _Node) -> str:
        if node.kind == "text":
            return node.value
        if node.kind == "var":
            value = self._evaluate(node.value)
            self.values.append("" if value is None else str(value))
            return f"{_SLOT_OPEN}{len(self.values) - 1}{_SLOT_CLOSE}"
        if node.kind == "include":
            return self.include(node.value)
        chosen = bool(self._evaluate(node.value)) == (node.kind == "if")
        return self.nodes((node.body if chosen else node.other) or [])

    def include(self, name: str) -> str:
        if name not in self.fragments:
            raise _error(self.where, f"unknown fragment {name!r}")
        if name in self.including:
            raise _error(self.where, f"fragment {name!r} includes itself in a loop")
        self.including.append(name)
        try:
            return self.nodes(_parse(self.fragments[name], self.where))
        finally:
            self.including.pop()


def fold(text: str) -> str:
    """Join wrapped paragraph lines with spaces; keep blank lines, list items,
    headings, and indented lines as they are."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        previous = lines[-1] if lines else ""
        if (
            line
            and previous
            and not _STRUCTURAL.match(line)
            and not _STRUCTURAL.match(previous)
        ):
            lines[-1] = f"{previous} {line.lstrip()}"
        else:
            lines.append(line)
    return _PARAGRAPH_BREAK.sub("\n\n", "\n".join(lines)).strip()


def render(
    text: str,
    variables: Mapping[str, object],
    *,
    fragments: Mapping[str, str] | None = None,
    where: str = "",
) -> str:
    renderer = _Renderer(variables, fragments or {}, where)
    folded = fold(renderer.nodes(_parse(text, where)))
    return _SLOT_PATTERN.sub(lambda m: renderer.values[int(m.group(1))], folded)


def _names(
    nodes: list[_Node], fragments: Mapping[str, str], seen: set[str]
) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if node.kind in {"var", "if", "unless"}:
            found |= names(node.value)
        if node.kind == "include" and node.value not in seen:
            if node.value not in fragments:
                raise TemplateError(f"unknown fragment {node.value!r}")
            found |= _names(
                _parse(fragments[node.value], ""), fragments, seen | {node.value}
            )
        found |= _names(node.body or [], fragments, seen)
        found |= _names(node.other or [], fragments, seen)
    return found


def template_names(
    text: str, *, fragments: Mapping[str, str] | None = None
) -> set[str]:
    """Every name `text` reads, including through blocks and fragments."""
    return _names(_parse(text, ""), fragments or {}, set())

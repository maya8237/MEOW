"""The small, safe expression language of workflow `when` and template `#if`.

Grammar, loosest binding first:

    expr       := and ("or" and)*
    and        := not ("and" not)*
    not        := "not" not | comparison
    comparison := atom (("==" | "!=" | "<" | "<=" | ">" | ">=") atom)?
    atom       := name | string | integer | "true" | "false" | "(" expr ")"

A name is a dotted path into plain data (`steps.review.status`). Its first
part must exist in the scope; a later part that is missing evaluates to None,
so a condition can ask about a step that has not run yet.
"""

import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<number>\d+)"
    r"|(?P<string>'[^']*'|\"[^\"]*\")"
    r"|(?P<op>==|!=|<=|>=|<|>|\(|\))"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)"
    r")"
)
_KEYWORDS = frozenset({"and", "or", "not", "true", "false"})
_COMPARE: dict[str, Callable[[object, object], object]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


_LEAVES: dict[str, Callable[[str], "Expr"]] = {
    "number": lambda value: Expr("literal", int(value)),
    "string": lambda value: Expr("literal", value[1:-1]),
    "true": lambda _value: Expr("literal", True),
    "false": lambda _value: Expr("literal", False),
    "name": lambda value: Expr("name", value),
}


class ExpressionError(ValueError):
    """An expression does not parse, or names something the scope lacks."""


@dataclass(frozen=True)
class Expr:
    """A parsed expression node: `kind` is literal, name, not, and, or, or an
    operator from `_COMPARE`."""

    kind: str
    value: object = None
    children: tuple["Expr", ...] = ()


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens = []
    position = 0
    stripped = text.rstrip()
    while position < len(stripped):
        match = _TOKEN.match(stripped, position)
        if match is None or match.end() == position:
            raise ExpressionError(
                f"unexpected {stripped[position:].strip()[:20]!r} in {text!r}"
            )
        kind = match.lastgroup or ""
        value = match.group(kind)
        if kind == "name" and value in _KEYWORDS:
            kind = value
        tokens.append((kind, value))
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokenize(text)
        self.index = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self, kind: str) -> bool:
        token = self._peek()
        if token is not None and (token[0] == kind or token[1] == kind):
            self.index += 1
            return True
        return False

    def parse(self) -> Expr:
        if not self.tokens:
            raise ExpressionError("empty expression")
        node = self._or()
        if self._peek() is not None:
            raise ExpressionError(
                f"unexpected {self._peek()[1]!r} in {self.text!r}"  # type: ignore[index]
            )
        return node

    def _or(self) -> Expr:
        node = self._and()
        while self._take("or"):
            node = Expr("or", children=(node, self._and()))
        return node

    def _and(self) -> Expr:
        node = self._not()
        while self._take("and"):
            node = Expr("and", children=(node, self._not()))
        return node

    def _not(self) -> Expr:
        if self._take("not"):
            return Expr("not", children=(self._not(),))
        return self._comparison()

    def _comparison(self) -> Expr:
        node = self._atom()
        token = self._peek()
        if token is not None and token[0] == "op" and token[1] in _COMPARE:
            self.index += 1
            node = Expr(token[1], children=(node, self._atom()))
        return node

    def _atom(self) -> Expr:
        token = self._peek()
        if token is None:
            raise ExpressionError(f"expression {self.text!r} ends too early")
        kind, value = token
        self.index += 1
        if kind in _LEAVES:
            return _LEAVES[kind](value)
        if value != "(":
            raise ExpressionError(f"unexpected {value!r} in {self.text!r}")
        node = self._or()
        if not self._take(")"):
            raise ExpressionError(f"missing ')' in {self.text!r}")
        return node


def parse(text: str) -> Expr:
    return _Parser(text).parse()


def _lookup(path: str, scope: Mapping[str, object]) -> object:
    head, *rest = path.split(".")
    if head not in scope:
        raise ExpressionError(f"unknown name {head!r}")
    value = scope[head]
    for part in rest:
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


def _compare(node: Expr, scope: Mapping[str, object]) -> object:
    left, right = (_evaluate(child, scope) for child in node.children)
    try:
        return _COMPARE[node.kind](left, right)
    except TypeError as exc:
        raise ExpressionError(f"cannot compare {left!r} {node.kind} {right!r}") from exc


_NODES: dict[str, Callable[[Expr, Mapping[str, object]], object]] = {
    "literal": lambda node, _scope: node.value,
    "name": lambda node, scope: _lookup(str(node.value), scope),
    "not": lambda node, scope: not _evaluate(node.children[0], scope),
    "and": lambda node, scope: all(_evaluate(c, scope) for c in node.children),
    "or": lambda node, scope: any(_evaluate(c, scope) for c in node.children),
}


def _evaluate(node: Expr, scope: Mapping[str, object]) -> object:
    return _NODES.get(node.kind, _compare)(node, scope)


def evaluate(text: str, scope: Mapping[str, object]) -> object:
    """Evaluate `text` against `scope`; raise ExpressionError on any problem."""
    return _evaluate(parse(text), scope)


def _names(node: Expr) -> set[str]:
    if node.kind == "name":
        return {str(node.value)}
    found: set[str] = set()
    for child in node.children:
        found |= _names(child)
    return found


def names(text: str) -> set[str]:
    """Every name `text` reads, with dotted paths kept whole."""
    return _names(parse(text))

"""Normalize SDK result usage and attach it to active run journals."""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from claude_agent_sdk import ResultMessage

if TYPE_CHECKING:
    from collections.abc import Iterator

    from meow.execution.run_state import RunStore

_active_usage: ContextVar[tuple["RunStore", str] | None] = ContextVar(
    "meow_active_usage", default=None
)


def _number(value: object, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if (
        not math.isfinite(value)
        or value < 0
        or (integer and not isinstance(value, int))
    ):
        return None
    return value


def usage_entry(role: str, result: object) -> dict[str, object]:
    """Return JSON-safe reported usage; unknown values remain unknown."""
    raw = getattr(result, "usage", None)
    breakdown = (
        {
            key: number
            for key, value in raw.items()
            if isinstance(key, str) and (number := _number(value)) is not None
        }
        if isinstance(raw, dict)
        else {}
    )
    input_tokens = _number(breakdown.get("input_tokens"), integer=True)
    output_tokens = _number(breakdown.get("output_tokens"), integer=True)
    tokens = (
        sum(value for value in (input_tokens, output_tokens) if value is not None)
        if input_tokens is not None or output_tokens is not None
        else None
    )
    return {
        "role": role,
        "turns": _number(getattr(result, "num_turns", None), integer=True),
        "duration_ms": _number(getattr(result, "duration_ms", None), integer=True),
        "tokens": tokens,
        "cost_usd": _number(getattr(result, "total_cost_usd", None)),
        "breakdown": breakdown or None,
    }


def usage_totals(value: object) -> dict[str, object]:
    """Sum known entries; a field with no reported values stays unavailable."""
    entries = value.get("entries", []) if isinstance(value, dict) else []
    totals: dict[str, object] = {}
    for field in ("turns", "duration_ms", "tokens", "cost_usd"):
        numbers = [
            number
            for entry in entries
            if isinstance(entry, dict)
            if (number := _number(entry.get(field))) is not None
        ]
        totals[field] = sum(numbers) if numbers else None
    return totals


@contextmanager
def usage_scope(store: "RunStore", run_id: str) -> "Iterator[None]":
    token = _active_usage.set((store, run_id))
    try:
        yield
    finally:
        _active_usage.reset(token)


def record_result(role: str, message: object) -> None:
    scope = _active_usage.get()
    if scope is not None and isinstance(message, ResultMessage):
        store, run_id = scope
        store.add_usage(run_id, usage_entry(role, message))

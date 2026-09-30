"""
meow/native_state.py

On-disk round-counter persistence for native (in-Claude-Code-session)
execution -- the `round_state` helper `native.py` re-exports for
`native_cli.py` to wire to `meow native round`. Split out on its own because
tracking how many generator<->reviewer rounds a skill-driven sprint has run
is a distinct concern from directory bootstrapping (`native_prepare.py`),
lint execution (`native_lint.py`), or prompt construction
(`native_prompt.py`).
"""

import json
from pathlib import Path

STATE_SUFFIX = ".native-state.json"
ROUND_MODES = ("next", "reset", "show")


def _state_file(plan_file: Path) -> Path:
    return plan_file.with_name(plan_file.stem + STATE_SUFFIX)


def _read_round(state_file: Path) -> int:
    """The stored round number; a missing or unreadable file means 0."""
    try:
        value = json.loads(state_file.read_text(encoding="utf-8"))["round"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return value if isinstance(value, int) and value >= 0 else 0


def round_state(plan_file: Path, max_rounds: int, mode: str) -> dict:
    """Advance, reset, or read the on-disk round counter for a plan.

    `next` starts a new round unless `max_rounds` rounds already ran, in
    which case it leaves the counter alone and reports `exhausted` -- the
    skill must stop and report instead of looping. The counter lives beside
    the plan (not in the model's memory) so it survives context compaction.
    """
    if mode not in ROUND_MODES:
        raise ValueError(f"mode must be one of {ROUND_MODES}, got {mode!r}")
    state_file = _state_file(plan_file)
    current = _read_round(state_file)
    if mode == "reset":
        current = 0
    exhausted = mode == "next" and current >= max_rounds
    if mode == "next" and not exhausted:
        current += 1
    if mode != "show" and not exhausted:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"round": current}), encoding="utf-8")
    return {"round": current, "max_rounds": max_rounds, "exhausted": exhausted}

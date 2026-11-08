"""Keep a run on the kit commits it started with.

`RunStore.create` records the commits in effect (`current_pins`) and exports
them in `MEOW_KIT_PINS`, which kit loading honors, so the run's own processes
and the workers they start never see a kit update that lands mid-run.
`meow resume` re-applies the recorded pins (`pinned`), downloading a commit
again if its cached copy was pruned.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from meow.project.custom.kit_store import KitUnavailable, fetch, kit_dir, normalize_url
from meow.project.custom.kits import (
    PINS_ENV,
    collect_specs,
    effective_entry,
    kits_home,
    lock_file,
    pin_key,
    pinned_entry,
    read_lock,
)


def current_pins(project_dir: Path, user_dir: Path) -> dict[str, dict]:
    """The commit of every installed kit whose files are on disk."""
    home = kits_home(user_dir)
    pins = {}
    for spec in collect_specs(project_dir, user_dir):
        locked = read_lock(lock_file(spec.scope, project_dir, user_dir)).get(spec.name)
        entry = pinned_entry(spec, effective_entry(spec, locked), home) or {}
        url, sha = normalize_url(spec.url), entry.get("sha")
        if sha and kit_dir(home, url, sha).is_dir():
            hooks = entry.get("hooks_sha") or sha
            pins[pin_key(spec)] = {"url": url, "sha": sha, "hooks_sha": hooks}
    return pins


def adopt(pins: dict[str, dict]) -> None:
    """Make this process, and everything it starts, use `pins`."""
    if pins:
        os.environ[PINS_ENV] = json.dumps(pins, sort_keys=True)


def _restore(previous: str | None) -> None:
    if previous is None:
        os.environ.pop(PINS_ENV, None)
    else:
        os.environ[PINS_ENV] = previous


def _usable(pin: dict, home: Path) -> str | None:
    """Download the pinned commits again if needed; the failure reason, if any."""
    try:
        for sha in {pin["sha"], pin.get("hooks_sha") or pin["sha"]}:
            fetch(home, pin["url"], sha, timeout=60)
    except (KitUnavailable, ValueError) as exc:
        return str(exc).splitlines()[0] if str(exc) else "download failed"
    return None


def _prepare(
    pins: dict[str, dict], project_dir: Path, user_dir: Path
) -> tuple[dict[str, dict], list[str]]:
    home = kits_home(user_dir)
    specs = {pin_key(spec): spec for spec in collect_specs(project_dir, user_dir)}
    usable, notes = {}, []
    for key, pin in sorted(pins.items()):
        name = key.split("/", 1)[-1]
        if key not in specs or normalize_url(specs[key].url) != pin.get("url"):
            notes.append(
                f"kit {name}: installed differently now; the run's copy is unused"
            )
        elif reason := _usable(pin, home):
            notes.append(
                f"kit {name}: could not restore {pin['sha'][:7]} ({reason}); "
                "using the installed version"
            )
        else:
            usable[key] = pin
            locked = read_lock(lock_file(specs[key].scope, project_dir, user_dir))
            current = effective_entry(specs[key], locked.get(specs[key].name)) or {}
            if current.get("sha") not in {None, pin["sha"]}:
                notes.append(
                    f"kit {name}: using {pin['sha'][:7]}, the version this run "
                    f"started with (installed: {current['sha'][:7]})"
                )
    return usable, notes


@contextmanager
def pinned(
    pins: dict[str, dict], project_dir: Path, user_dir: Path
) -> Iterator[list[str]]:
    """Run a block on `pins`, restoring the previous setting afterwards."""
    previous = os.environ.get(PINS_ENV)
    usable, notes = _prepare(pins, project_dir, user_dir) if pins else ({}, [])
    if usable:
        os.environ[PINS_ENV] = json.dumps(usable, sort_keys=True)
    try:
        yield notes
    finally:
        _restore(previous)


def active_run_shas(project_dir: Path, remote: str) -> set[str]:
    """Commits of `remote` that unfinished runs in this project still pin."""
    from meow.execution.run_state import TERMINAL_PHASES, RunStore

    shas: set[str] = set()
    for path in RunStore(project_dir).record_paths():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("phase") in TERMINAL_PHASES:
            continue
        for pin in (record.get("kit_pins") or {}).values():
            if isinstance(pin, dict) and pin.get("url") == remote:
                shas |= {pin.get("sha"), pin.get("hooks_sha")} - {None}
    return shas

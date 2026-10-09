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

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from meow.execution.delivery import deliver_verified_run
from meow.execution.run_state import RunStore
from meow.infrastructure.checks import (
    code_revision,
    completion_ready,
    config_fingerprint,
    configured_checks,
    run_final_checks,
)
from meow.project.config import config_paths, load_config

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


def checkpoint(  # ruff: ignore[too-many-arguments, complex-structure, too-many-statements, too-many-branches]
    repo: Path,
    active_dir: Path,
    phase: str,
    *,
    run_id: str | None = None,
    request: str = "",
    plan_file: Path | None = None,
    review_file: Path | None = None,
    round_num: int | None = None,
    reviewer: str | None = None,
    tester: str | None = None,
) -> dict:
    """Persist one native phase using the CLI run journal schema."""
    if phase == "complete":
        raise ValueError("Native completion requires current gate evidence")
    store = RunStore(repo)
    patch: dict = {}
    if run_id is None:
        branch = (
            subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=active_dir,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or "detached"
        )
        record = store.create(
            source="native",
            request=request,
            repo=repo,
            worktree=active_dir,
            branch=branch,
        )
        run_id = record.id
    else:
        record = store.load(run_id)
    if plan_file is not None:
        plan_file = plan_file.resolve()
        patch["plan_file"] = str(plan_file)
        patch["plan_fingerprint"] = hashlib.sha256(plan_file.read_bytes()).hexdigest()
    if review_file is not None:
        patch["review_file"] = str(review_file.resolve())
    if round_num is not None:
        patch["round"] = round_num
    if reviewer is not None or tester is not None:
        patch["results"] = {
            **({"reviewer": reviewer} if reviewer is not None else {}),
            **(
                {"reviewer_revision": code_revision(active_dir)}
                if reviewer is not None
                else {}
            ),
            **({"tester": tester} if tester is not None else {}),
        }
    if not record.config_fingerprint and config_paths(active_dir):
        patch["config_fingerprint"] = config_fingerprint(active_dir)
    record = store.transition(run_id, phase, **patch)
    return {
        "run_id": record.id,
        "phase": record.phase,
        "worktree": record.worktree,
        "branch": record.branch,
    }


async def finalize(  # ruff: ignore[too-many-statements]
    repo: Path, active_dir: Path, run_id: str
) -> dict:
    """Run current configured gates and seal a reviewed native run."""
    store = RunStore(repo)
    record = store.load(run_id)
    from meow.cli.resume_cli import _validate

    problem = _validate(record, repo)
    if problem:
        raise ValueError(problem)
    if Path(record.worktree).resolve() != active_dir.resolve():
        raise ValueError("Saved worktree differs from active directory")
    config = load_config(active_dir)
    store.transition(run_id, "checking")
    try:
        results = await run_final_checks(active_dir, config)
    except BaseException as exc:
        store.transition(run_id, "failed", last_failure=str(exc))
        raise
    store.transition(
        run_id,
        "checks_finished",
        results={
            "checks": [asdict(item) for item in results],
        },
    )
    verdicts = store.load(run_id).results
    tester_enabled = bool(config.get("tester", {}).get("tests"))
    passed = completion_ready(
        results,
        configured_checks(config),
        active_dir,
        verdicts.get("reviewer", "FAIL"),
        verdicts.get("tester", "FAIL") if tester_enabled else None,
        reviewer_revision=verdicts.get("reviewer_revision", ""),
    )
    if passed:
        deliver_verified_run(store, run_id)
        store.transition(run_id, "complete")
    else:
        store.transition(run_id, "failed", last_failure="Required evidence failed")
    return {"run_id": run_id, "complete": passed}

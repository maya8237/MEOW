"""Native run state: the on-disk round counter (`meow native round`) and the
run-journal checkpoints and gated finalize shared with CLI runs."""

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from meow.execution.cleanup import cleanup_completed
from meow.execution.delivery import deliver_verified_run
from meow.execution.run_state import RunStore
from meow.infrastructure.checks import (
    adopt_stage_artifacts,
    code_revision,
    completion_ready,
    config_fingerprint,
    configured_checks,
    run_final_checks,
    stage_baseline,
)
from meow.infrastructure.worktree import _is_linked_worktree, current_branch
from meow.project.config import config_paths, config_root, load_config
from meow.project.plan_files import plan_review_file

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


def _review_errors(
    repo: Path, active_dir: Path, review: Path | None, plan: Path | None
) -> list[str]:
    from meow.native.native_prepare import verdict
    from meow.native.native_review import review_report_errors

    if review is None:
        return ["Review report is missing"]
    if plan is not None and review.resolve() != plan_review_file(plan).resolve():
        return ["Review report does not match the saved plan"]
    errors = review_report_errors(review, repo, active_dir, plan_file=plan)
    if verdict(review)["status"] != "PASS":
        errors.append("Review report does not contain a verified PASS")
    return errors


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
    target_branch: str | None = None,
) -> dict:
    """Persist one native phase using the CLI run journal schema."""
    if phase == "complete":
        raise ValueError("Native completion requires current gate evidence")
    if tester == "PASS" and load_config(config_root(repo, active_dir)).get(
        "tester", {}
    ).get("tests"):
        raise ValueError("Configured tester PASS requires the CLI tester lifecycle")
    store = RunStore(repo)
    patch: dict = {}
    if run_id is None:
        record = store.create(
            source="native",
            request=request,
            repo=repo,
            worktree=active_dir,
            branch=current_branch(active_dir),
            delivery=(
                {
                    "base": target_branch
                    or load_config(config_root(repo, active_dir))["delivery"][
                        "target_branch"
                    ]
                }
                if _is_linked_worktree(active_dir)
                else {}
            ),
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
    if reviewer == "PASS":
        review = review_file or (
            Path(record.review_file) if record.review_file else None
        )
        plan = plan_file or (Path(record.plan_file) if record.plan_file else None)
        if errors := _review_errors(repo, active_dir, review, plan):
            raise ValueError("Review completion gates: " + "; ".join(errors))
        patch.setdefault("results", {})["reviewer_report_fingerprint"] = hashlib.sha256(
            review.read_bytes()
        ).hexdigest()
    if round_num is not None:
        patch["round"] = round_num
    if reviewer is not None or tester is not None:
        patch["results"] = {
            **patch.get("results", {}),
            **({"reviewer": reviewer} if reviewer is not None else {}),
            **(
                {"reviewer_revision": code_revision(active_dir)}
                if reviewer is not None
                else {}
            ),
            **({"tester": tester} if tester is not None else {}),
            **(
                {"tester_revision": code_revision(active_dir)}
                if tester is not None
                else {}
            ),
        }
    if phase in {"generator_started", "generator_finished"}:
        # Checkpoints can arrive out of order. A verdict recorded against an
        # older revision must not survive code written after it.
        revision = code_revision(active_dir)
        results = patch.setdefault("results", {})
        results["generator_revision"] = revision
        saved = record.results.get("reviewer_revision")
        if saved and saved != revision:
            results["reviewer"] = "STALE"
            results["reviewer_revision"] = ""
        tester_revision = record.results.get("tester_revision")
        if record.results.get("tester") and tester_revision != revision:
            results["tester"] = "STALE"
            results["tester_revision"] = ""
    if not record.config_fingerprint and config_paths(repo):
        patch["config_fingerprint"] = config_fingerprint(repo)
    record = store.transition(run_id, phase, **patch)
    return {
        "run_id": record.id,
        "phase": record.phase,
        "worktree": record.worktree,
        "branch": record.branch,
    }


async def finalize(  # ruff: ignore[too-many-statements, complex-structure, too-many-branches]
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
    config = load_config(config_root(repo, active_dir))
    tester_enabled = bool(config.get("tester", {}).get("tests"))
    if tester_enabled:
        reason = (
            "Configured tester validation requires the CLI tester lifecycle. "
            f"Continue this saved run with meow resume {run_id} --continue --repair "
            f'--work-dir "{repo}"; native mode cannot prove the tester lifecycle.'
        )
        store.transition(run_id, "failed", last_failure=reason)
        return {
            "run_id": run_id,
            "complete": False,
            "required_mode": "cli",
            "reason": reason,
        }
    review = Path(record.review_file) if record.review_file else None
    plan = Path(record.plan_file) if record.plan_file else None
    errors = _review_errors(repo, active_dir, review, plan)
    if not errors and hashlib.sha256(
        review.read_bytes()
    ).hexdigest() != record.results.get("reviewer_report_fingerprint"):
        errors.append("Review report changed after its checkpoint")
    if errors:
        reason = "Review completion gates: " + "; ".join(errors)
        store.transition(run_id, "failed", last_failure=reason)
        return {"run_id": run_id, "complete": False, "reason": reason}
    store.transition(run_id, "checking")
    try:
        baseline = await asyncio.to_thread(stage_baseline, active_dir)
        results = await run_final_checks(active_dir, config)
        await asyncio.to_thread(
            adopt_stage_artifacts, store, run_id, active_dir, baseline
        )
    except BaseException as exc:
        from meow.execution.sprint_runner import _settle_failure

        _settle_failure(store, run_id, exc)  # a crashed check parks resumable
        raise
    store.transition(
        run_id,
        "checks_finished",
        results={
            "checks": [asdict(item) for item in results],
        },
    )
    verdicts = store.load(run_id).results
    passed = (
        not _review_errors(repo, active_dir, review, plan)
        and hashlib.sha256(review.read_bytes()).hexdigest()
        == verdicts.get("reviewer_report_fingerprint")
        and completion_ready(
            results,
            configured_checks(config),
            active_dir,
            verdicts.get("reviewer", "FAIL"),
            reviewer_revision=verdicts.get("reviewer_revision", ""),
        )
    )
    if passed:
        deliver_verified_run(store, run_id)
        store.transition(run_id, "complete")
        cleanup_completed(repo, run_id)
    else:
        reason = "Required evidence failed"
        if verdicts.get("reviewer") == "STALE":
            reason = (
                "Review is stale: code changed after the reviewer verdict. "
                "Rerun the reviewer, checkpoint it, then finalize again"
            )
        elif verdicts.get("reviewer_revision") not in {None, ""} and verdicts.get(
            "reviewer_revision"
        ) != code_revision(active_dir):
            reason = (
                "Reviewed revision differs from the current code; rerun the "
                "reviewer, checkpoint it, then finalize again"
            )
        store.transition(run_id, "failed", last_failure=reason)
    return {"run_id": run_id, "complete": passed}

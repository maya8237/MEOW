"""The `mr_end` lifecycle hook: fired once a workflow has put its MR.

`fire_mr_end` always runs the built-in check (MR posted, pipeline verified the
way MEOW verifies it), then the user's `mr_end` hooks; problems and hook exit-2
stderr are shown (advisory, the run is unchanged). `python -m
meow.execution.mr_hook` runs the same check as a hook command.
"""

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from meow.infrastructure.user_hooks import run_lifecycle

OK_STATUSES = frozenset({"verified", "unvalidated"})


def mr_payload(kind: str, delivery: dict, **fields: object) -> dict:
    pr = delivery.get("pr")
    return {
        "kind": kind,  # "run" or "orchestrator"
        **fields,  # id, branch, base
        "mr_url": pr.get("url") if isinstance(pr, dict) else None,
        "mr_error": delivery.get("pr_error"),
        "pushed_commit": delivery.get("commit") or delivery.get("pushed_commit"),
        "pipeline": delivery.get("pipeline"),
    }


async def fire_mr_end(
    config: dict, payload: dict, cwd: Path, announce: Callable[[str], None]
) -> None:
    # Plain runs already verified the pipeline and printed the outcome.
    if payload["kind"] != "run":
        for found in await asyncio.to_thread(_builtin_problems, payload, cwd):
            announce(f"MR check: {found}")
    custom = config.get("customizations")
    hooks = custom.hooks if custom else ()
    for note in await run_lifecycle(hooks, "mr_end", payload, cwd):
        announce(f"mr_end hook {note}")


def _builtin_problems(payload: dict, cwd: Path) -> list[str]:
    try:
        return problems(payload, cwd)
    except Exception as exc:  # the built-in check must never fail a delivered run
        return [f"check failed: {exc}"]


def _judge(snap, judged, fix_non_meow: bool) -> dict:
    """Outcome like `verify_pipeline`: meow-review must pass; other failing jobs
    count too, worded by `fix_non_meow_jobs` (deploy-like jobs are never fixed)."""
    failing = [j.name for j in snap.jobs if j.status == "failed"]
    out = {"status": "verified", "failing": failing, "url": snap.url, "detail": ""}
    if judged.review_failed:
        out.update(status="not_verified", detail="meow review job failed")
    elif judged.other_failed:
        fixable = fix_non_meow and judged.fixable_other
        out.update(
            status="not_verified",
            detail="failing jobs not fixed" if fixable else "failing jobs left",
        )
    return out


def _live_pipeline(payload: dict, cwd: Path) -> dict | None:
    """Read-only pipeline result for runs that did not verify it themselves."""
    from meow.execution.orchestration.pull_request import origin_url
    from meow.execution.pipeline_classify import classify
    from meow.integrations.pipeline_api import connect
    from meow.project.config import load_config
    from meow.project.config_schema import PIPELINE_DEFAULTS

    config = load_config(cwd)
    settings = {**PIPELINE_DEFAULTS, **config.get("pipeline", {})}
    sha = payload.get("pushed_commit")
    provider = connect(origin_url(cwd) or "", config, os.environ)
    deadline = time.monotonic() + float(settings["timeout"])
    while sha and time.monotonic() < deadline:
        snap = provider.find_pipeline(sha)
        judged = (
            classify(snap, fix_non_meow=settings["fix_non_meow_jobs"]) if snap else None
        )
        if snap and snap.jobs and not judged.running:
            return _judge(snap, judged, bool(settings["fix_non_meow_jobs"]))
        time.sleep(float(settings["poll_seconds"]))
    return None


def _pipeline_problem(pipeline: dict | None) -> list[str]:
    if not pipeline:
        return ["no finished pipeline found for the pushed commit"]
    if pipeline.get("status") in OK_STATUSES:
        return []
    failing = ", ".join(pipeline.get("failing") or ())
    detail = pipeline.get("detail") or pipeline.get("status")
    suffix = f" (failing: {failing})" if failing else ""
    return [f"pipeline not verified: {detail}{suffix}"]


def problems(payload: dict, cwd: Path) -> list[str]:
    """What is wrong with the delivered MR; empty when it is as it should be."""
    if not payload.get("mr_url"):
        return [f"merge request not posted: {payload.get('mr_error') or 'unknown'}"]
    pipeline = payload.get("pipeline")
    if pipeline is None:
        try:
            pipeline = _live_pipeline(payload, cwd)
        except Exception as exc:  # no token or provider: report, do not crash
            return [f"pipeline could not be checked: {exc}"]
    return _pipeline_problem(pipeline)


def main() -> int:
    found = problems(json.load(sys.stdin), Path.cwd())
    print("\n".join(found), file=sys.stderr)
    return 2 if found else 0


if __name__ == "__main__":
    sys.exit(main())

"""The `mr_end` lifecycle hook: fired once a workflow has put its MR.

`fire_mr_end` runs the user's `mr_end` hooks with the MR and pipeline outcome;
a hook that exits 2 has its stderr shown (advisory, the run is unchanged).
`python -m meow.execution.mr_hook` is a ready-made hook command that checks the
MR was posted and the pipeline was verified the way MEOW verifies it.
"""

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
    custom = config.get("customizations")
    hooks = custom.hooks if custom else ()
    for note in await run_lifecycle(hooks, "mr_end", payload, cwd):
        announce(f"mr_end hook {note}")


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
            failed = [j.name for j in snap.jobs if j.status == "failed"]
            status = "not_verified" if failed else "verified"
            return {"status": status, "failing": failed, "url": snap.url}
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

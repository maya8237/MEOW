"""Final delivery of a plain run: open the merge/pull request for its branch,
then let the pipeline verify it (`run_final_delivery`, after `deliver`)."""

import os
import re
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.fixers import LintFixAgent, ReviewFixAgent
from meow.agents.orchestration import OrchestratorAgents
from meow.execution.orchestration.pull_request import (
    detect_provider,
    manual_pr_link,
    origin_url,
    provider_mcp,
)
from meow.execution.pipeline_verify import (
    PipelineFixers,
    PipelineOutcome,
    verify_pipeline,
)
from meow.execution.pipeline_verify import _one_line as _capped_line
from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled
from meow.infrastructure.logging import get_logger
from meow.integrations.pipeline_api import PipelineUnavailable, connect
from meow.project.config_schema import PIPELINE_DEFAULTS

logger = get_logger(__name__)
_TITLE_LIMIT = 72
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s@]*@", re.IGNORECASE)
_HEADER = re.compile(
    r"\b(authorization|private-token|job-token)\b(['\"]?\s*[=:]\s*['\"]?)"
    r"(?:(?:bearer|basic|token)\s+)?[^\s'\",}]+",
    re.IGNORECASE,
)
_SCHEME = re.compile(r"\b(bearer|basic)\s+[^\s'\",}]+", re.IGNORECASE)
_SECRET = re.compile(
    r"(\b\w*(?:TOKEN|KEY|SECRET|PASSWORD)\w*['\"]?\s*[=:]\s*['\"]?)"
    r"(?:(?:bearer|basic)\s+)?[^\s'\",}]+",
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    text = _USERINFO.sub(r"\1", text)
    text = _HEADER.sub(r"\1\2***", text)
    text = _SECRET.sub(r"\1***", text)
    return _SCHEME.sub(r"\1 ***", text)


def _one_line(exc: Exception) -> str:
    """One scrubbed, env-redacted line capped like the pipeline loop's details."""
    return _capped_line(_scrub(" ".join(str(exc).split()))) or type(exc).__name__


def _body(record) -> str:
    lines = [
        record.request.strip(),
        "",
        f"MEOW run: `{record.id}`",
        "",
        "Local review and checks passed before delivery.",
    ]
    if record.plan_file:
        lines += ["", f"Plan: `{record.plan_file}`"]
    return "\n".join(lines)


async def _open(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per collaborator
    store: RunStore, record, config: dict, agents, remote: str | None, provider
) -> str:
    base = record.delivery.get("base")
    if not base:
        raise RuntimeError("no base branch recorded")
    if not remote:
        raise RuntimeError("no origin remote is configured")
    if provider is None:
        raise RuntimeError(
            "no GitHub or GitLab integration matches the origin remote "
            f"{_USERINFO.sub(r'\\1', remote)}"
        )
    mcp = provider_mcp(config, provider)
    context = ProjectContext(Path(record.worktree), config, use_worktree=True)
    lines = (line.strip() for line in record.request.splitlines())
    first = next((line for line in lines if line), record.id)
    return await agents.pull_request(context, mcp, provider).open(
        f"MEOW: {first[:_TITLE_LIMIT]}",
        _body(record),
        record.branch,
        base,
        store.directory / f"{record.id}.pull-request.json",
    )


async def open_run_merge_request(
    store: RunStore, run_id: str, config: dict, agents: OrchestratorAgents
) -> dict | None:
    """Open the MR/PR for the run branch into `delivery["base"]`.

    Returns `{"url", "provider"}`, the saved value when already opened, or None
    after recording why it could not be opened. Provider problems never fail a
    run; cancellation still propagates.
    """
    record = store.load(run_id)
    if record.delivery.get("pr"):
        return record.delivery["pr"]
    remote = origin_url(Path(record.repo))
    provider = detect_provider(remote, config) if remote else None
    pr = error = link = None
    try:
        url = await _open(store, record, config, agents, remote, provider)
        pr = {"url": url, "provider": provider}
    except RunCancelled:
        raise
    except Exception as exc:  # provider problems must not fail a delivered run
        error = _one_line(exc)
        base = record.delivery.get("base")
        if remote and base:
            link = manual_pr_link(remote, provider, base, record.branch)
    delivery = {**record.delivery, "pr": pr, "pr_error": error, "pr_manual_link": link}
    store.transition(run_id, record.phase, delivery=delivery)
    return pr


_SKIPPED = "Merge request and pipeline verification skipped (--no-pr / open_mr=false)."
_ADVICE = "fix the failing jobs on the MR branch, or re-run the pipeline."


def _warn(exc: Exception) -> None:
    with suppress(OSError):  # logging to a closed stream must not fail the stage
        logger.warning("final_delivery_journal_failed", error=_one_line(exc))


def _journal(store: RunStore, run_id: str, phase: str | None, **delivery) -> bool:
    """Merge `delivery` into the journal; a write failure is logged, never raised."""
    try:
        record = store.load(run_id)
        merged = {**record.delivery, **delivery}
        store.transition(run_id, phase or record.phase, delivery=merged)
    except Exception as exc:  # the stage must not fail an already-pushed run
        _warn(exc)
        return False
    return True


def _announcer(store: RunStore, run_id: str):
    """Print a `[delivery]` line and mirror it to `delivery["stage"]`."""

    def announce(text: str) -> None:
        with suppress(OSError):  # a closed stdout (BrokenPipeError) loses the line
            print(f"[delivery] {text}", flush=True)
        _journal(store, run_id, None, stage=text)

    return announce


def _not_verified(detail: str) -> PipelineOutcome:
    return PipelineOutcome("not_verified", 0, "", (), detail)


def _final_text(outcome: PipelineOutcome, mr_url: str | None) -> str:
    """The closing line; only a `verified` outcome reads "Verified by pipeline:"."""
    if outcome.status == "verified":
        return f"Verified by pipeline: {outcome.url}"
    if outcome.status == "unvalidated":
        detail = outcome.detail or "the provider could not validate it"
        return (
            f"Verified by pipeline (pipeline file not validated: {detail}): "
            f"{outcome.url}"
        )
    text = f"Not verified by pipeline: {outcome.detail or outcome.status}"
    unnamed = [name for name in outcome.failing if name not in text]
    if unnamed:
        text += "; failing jobs: " + ", ".join(unnamed)
    if mr_url:
        text += f". MR: {mr_url}"
    if outcome.url:
        text += f". See {outcome.url}; {_ADVICE}"
    return text


def _fixers(worktree: Path, config: dict) -> PipelineFixers:
    def context() -> ProjectContext:
        return ProjectContext(worktree, config, use_worktree=True)

    return PipelineFixers(
        lint=lambda: LintFixAgent(context()), review=lambda: ReviewFixAgent(context())
    )


async def _verify(state, settings: dict, announce) -> PipelineOutcome:
    store, run_id, config = state.store, state.run_id, state.sprint.config
    _journal(store, run_id, "pipeline_waiting")  # active: `meow cancel` works
    record = store.load(run_id)
    announce(
        "Feature complete. MEOW loop finished; entering final delivery "
        f"(MR into {record.delivery['base']})."
    )
    pr = await open_run_merge_request(store, run_id, config, OrchestratorAgents())
    if pr is None:
        reason = store.load(run_id).delivery.get("pr_error") or "unknown reason"
        return _not_verified(f"merge request not opened: {reason}")
    announce(
        f"MR opened: {pr['url']}. Waiting for the pipeline to verify the delivery."
    )
    try:
        provider = connect(origin_url(Path(record.repo)) or "", config, os.environ)
    except PipelineUnavailable as exc:
        return _not_verified(_one_line(exc))
    fixers = _fixers(Path(record.worktree), config)
    return await verify_pipeline(store, run_id, provider, fixers, settings, announce)


async def _guarded(state, settings: dict, announce) -> PipelineOutcome:
    """The stage never raises but for cancellation: the code is already pushed."""
    try:
        return await _verify(state, settings, announce)
    except RunCancelled:
        raise
    except Exception as exc:  # anything else must not fail a delivered run
        return _not_verified(_one_line(exc))


def _settle(store: RunStore, run_id: str, outcome: PipelineOutcome) -> PipelineOutcome:
    """Journal the outcome and return the run to the settled `delivered` phase.

    Keeps the journaled round count when the stage ended before the loop did.
    """
    try:
        stored = store.load(run_id).delivery.get("pipeline")
        stored = stored if isinstance(stored, dict) else {}
        rounds = max(outcome.rounds, int(stored.get("rounds") or 0))
    except Exception as exc:  # an unreadable journal keeps the outcome as is
        _warn(exc)
        rounds = outcome.rounds
    outcome = replace(outcome, rounds=rounds)
    pipeline = {
        "status": outcome.status,
        "rounds": outcome.rounds,
        "url": outcome.url,
        "failing": list(outcome.failing),
        "detail": outcome.detail,
    }
    _journal(store, run_id, "delivered", pipeline=pipeline)
    return outcome


def _stored_verified(delivery: dict) -> PipelineOutcome | None:
    stored = delivery.get("pipeline")
    if not isinstance(stored, dict) or stored.get("status") != "verified":
        return None
    failing = tuple(stored.get("failing") or ())
    rounds, url = int(stored.get("rounds") or 0), str(stored.get("url") or "")
    return PipelineOutcome(
        "verified", rounds, url, failing, str(stored.get("detail") or "")
    )


def _mr_url(store: RunStore, run_id: str) -> str | None:
    try:
        pr = store.load(run_id).delivery.get("pr")
    except Exception:  # the final line is still printed without the MR link
        return None
    return pr.get("url") if isinstance(pr, dict) else None


async def run_final_delivery(state) -> PipelineOutcome | None:
    """Open the MR into `delivery["base"]` and verify it through the pipeline.

    Does nothing without a base branch or a push. Never raises but for
    cancellation; a verified outcome is not re-run on resume.
    """
    store, run_id = state.store, state.run_id
    delivery = store.load(run_id).delivery
    if not delivery.get("base") or not delivery.get("push", True):
        return None
    announce = _announcer(store, run_id)
    pipeline = state.sprint.config.get("pipeline", {})
    options = delivery.get("pipeline_options", {})
    settings = {**PIPELINE_DEFAULTS, **pipeline, **options}
    if not settings["open_mr"]:
        announce(_SKIPPED)
        return None
    outcome = _stored_verified(delivery)
    if outcome is None:
        outcome = await _guarded(state, settings, announce)
    outcome = _settle(store, run_id, outcome)
    announce(_final_text(outcome, _mr_url(store, run_id)))
    return outcome

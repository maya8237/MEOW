# Evidence-Backed Quality Concerns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain concrete maintenance concerns found during a run and surface only relevant, evidence-backed concerns later.

**Architecture:** Store a small typed concern record with repository location, evidence, impact, suggested follow-up, first/last-seen run IDs, and state. Review and evaluation stages may emit candidates; a deterministic deduplicator keys them by location and issue identity. The run summary shows only concerns material to the current change. There is no aggregate numerical quality score.

**Tech Stack:** Python, existing reviewer/evaluation output, atomic JSON, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Quality concerns.”

## Global Constraints

- Every concern links to observable code, test, or verification evidence.
- A concern is advisory unless an existing required verification gate independently fails.
- No overall numerical quality score and no automatically generated debt register full of generic advice.
- Normal feature runs do not emit documentation-update recommendations.

## Review Focus

- Repeated review rounds should not create duplicate concerns.
- A deleted file should retire or mark a concern stale, not retain a misleading active location.
- Agent prose without repository evidence should not become a persistent concern.
- A low-impact unrelated concern should not dominate the run summary.
- Corrupt concern storage should not change a passing gate to PASS or silently erase evidence.

---

## File map

- Create `src/meow/quality.py`: concern model, evidence validation, deduplication, lifecycle.
- Modify `src/meow/evaluation.py`, `src/meow/orchestrator/core.py`, `src/meow/run_state/core.py`, `src/meow/status_cli/core.py`.
- Test `tests/test_quality.py`, `tests/execution/test_run_journal.py`, `tests/cli/test_status_cli.py`.

### Task 1: Validate and deduplicate concerns

**Interface:** `Concern(id, path, evidence, impact, follow_up, first_run, last_run, state)`; `record_concerns(repo, run_id, candidates) -> list[Concern]` rejects candidates without local evidence and deduplicates stable identities.

- [ ] Add failing tests for one supported concern, duplicate rounds, nonexistent evidence, path escape, and changed/deleted locations.
- [ ] Run `rtk pytest tests/test_quality.py -q` and confirm red.
- [ ] Implement bounded JSON storage with atomic writes and clear active/resolved/stale states.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: retain evidence-backed quality concerns`.

### Task 2: Integrate with review and concise status

**Interface:** Reviewer/evaluation candidates pass through `record_concerns`; status and run summary show only active concerns relevant to changed paths, with verbose access to all evidence.

- [ ] Add failing tests for relevant versus unrelated concerns, a resolved concern, and a passing run with advisory concerns. Assert no numeric score is rendered.
- [ ] Run `rtk pytest tests/cli/test_status_cli.py tests/execution/test_run_journal.py -q` and confirm red.
- [ ] Connect review/evaluation extraction to the typed store, filter summary by changed paths, and document advisory semantics in `docs/CLI.md`.
- [ ] Run focused tests and project lint.
- [ ] Commit: `feat: surface material quality concerns`.

## Completion check

Review a fixture change that introduces a known duplicated boundary check. Confirm the concern cites the duplicated code, appears once across review rounds, and does not independently block delivery.

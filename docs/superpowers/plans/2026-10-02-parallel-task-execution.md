# Parallel Task Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute independent implementation slices concurrently while preserving ownership, recoverability, and final combined-code verification.

**Architecture:** Add a machine-readable task graph derived from the accepted plan. A scheduler starts only ready tasks with disjoint ownership in isolated worktrees; each task produces a patch/commit and verification evidence. An integrator applies results in dependency order, records conflicts without discarding work, and runs one whole-branch review and required gates on the combined revision. One-task plans keep the current generator path.

**Tech Stack:** Python `asyncio`, Git worktrees, existing planner/generator/reviewer/check modules, `RunStore`, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Task dependencies and parallel implementation.” Depends on cancellation, permissions, worktree controls, and final checks.

## Global Constraints

- No parallel workers share a writable checkout.
- Tasks with overlapping ownership or unresolved interface dependencies run sequentially.
- Integration conflicts preserve every worker branch/worktree and stop delivery.
- Completion requires whole-branch review and current required gates on the combined code.
- `--unattended` never asks for conflict resolution; it checkpoints the conflict and stops.

## Review Focus

- A dependency cycle is rejected before agents start.
- Two tasks with overlapping paths never run concurrently.
- A worker that fails after edits retains its checkout and evidence.
- A later task sees the integrated output of its dependencies.
- A cancellation stops outstanding workers and blocks integration/delivery.

---

## File map

- Create `src/meow/tasks/model.py`: task IDs, dependencies, ownership, state, and validation.
- Create `src/meow/tasks/scheduler.py`: ready-set scheduling and cancellation.
- Create `src/meow/tasks/integration.py`: ordered Git integration and conflict records.
- Modify `src/meow/sprint_runner/core.py`, `src/meow/run_state/core.py`, `src/meow/status_cli/core.py`, `src/meow/checks/core.py`.
- Test `tests/tasks/test_model.py`, `tests/tasks/test_scheduler.py`, `tests/tasks/test_integration.py`, `tests/execution/test_run_journal.py`.

### Task 1: Validate a bounded task graph

**Interface:** `TaskSpec(id, depends_on, owned_paths, verification)` and `TaskGraph.validate()` reject unknown IDs, cycles, and ambiguous ownership. A one-task graph is valid.

- [ ] Add failing tests for a direct one-task plan, a two-task dependency, cycle, unknown dependency, duplicate ID, and overlapping owners marked parallel. Example: tasks `api` and `ui` may run together only when their declared paths are disjoint.
- [ ] Run `rtk pytest tests/tasks/test_model.py -q` and confirm red.
- [ ] Implement typed validation and stable serialization into the run journal; cap graph size to avoid unbounded parallelism.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: validate implementation task graphs`.

### Task 2: Schedule isolated ready tasks

**Interface:** `run_task_graph(graph, worker, *, max_parallel, cancel) -> TaskResults` starts ready disjoint tasks, waits for dependencies, and records each start/finish/failure.

- [ ] Add failing tests with fake workers proving concurrency for disjoint tasks, sequencing for dependencies/overlap, bounded worker count, cancellation, and failure preservation.
- [ ] Run `rtk pytest tests/tasks/test_scheduler.py -q` and confirm red.
- [ ] Implement scheduling over dedicated worktrees and role-scoped policy; journal transitions before and after each worker. Keep the existing sequential generator for one-task graphs.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: schedule isolated implementation tasks`.

### Task 3: Integrate and verify combined code

**Interface:** `integrate_results(base, results) -> IntegrationResult` records applied commits and conflict details without deleting task worktrees.

- [ ] Add fixture-repo tests for two disjoint commits, a dependency chain, a merge conflict, and a failed worker. Assert conflicts leave all branches and prohibit delivery.
- [ ] Run `rtk pytest tests/tasks/test_integration.py -q` and confirm red.
- [ ] Implement ordered integration; then invoke existing reviewer and `run_final_checks` on the combined revision. Store the verified revision in the run record.
- [ ] Run focused tests, whole-run journal tests, and `rtk ruff check`.
- [ ] Commit: `feat: integrate and verify parallel task output`.

### Task 4: Show task progress and recovery

**Interface:** `meow status` shows completed, running, waiting, and failed tasks, with ownership and recovery details in verbose mode.

- [ ] Add failing status tests for an active graph, a conflict, and cancellation. Verify no status read starts a worker.
- [ ] Run `rtk pytest tests/cli/test_status_cli.py -q` and confirm red.
- [ ] Render task progress from the journal and document recovery behavior in `docs/CLI.md`.
- [ ] Run focused tests and project lint.
- [ ] Commit: `feat: report task graph progress`.

## Completion check

Run a fixture repository with two independent tasks and a dependent third task. Confirm only the final integrated revision is reviewed and gated. Run a forced conflict and confirm no delivery or worktree deletion.

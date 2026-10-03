# Background Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a local `meow run ... --unattended --background` continue after its launching terminal closes, with status, cancellation, and recovery by run ID.

**Architecture:** The foreground CLI creates a checkpoint and starts a supervised worker process with a unique run ID and dedicated log. A process identity record prevents accidental control of an unrelated PID. The worker uses the same `run_sprint` path and checkpoints as foreground execution; status reports liveness and terminal result, and cancel requests a cooperative stop before any forced signal. A startup reconciliation marks orphaned runs interrupted rather than claiming success.

**Tech Stack:** Python subprocess/process groups on Windows and POSIX, existing `RunStore`, `meow status/cancel/resume`, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Background execution.” Depends on reliable cancellation and recovery.

## Global Constraints

- `--background` requires `--unattended`; it must not create interactive prompts.
- Never launch the same run ID twice or signal a process whose identity cannot be verified.
- Worker stdout/stderr go to a run-owned log; secrets are redacted from status/checkpoints.
- Worker loss is interrupted/unknown, never a passing run.
- Do not build a recurring scheduler or notification service as part of the basic worker.

## Review Focus

- Parent terminal exits immediately while the worker continues.
- A stale or reused PID is not treated as MEOW's worker.
- A crash before worker registration does not leave a falsely running record.
- Cancellation stops an active background run and prevents delivery.
- A machine restart leaves a recoverable interrupted record, not a phantom active run.

---

## File map

- Create `src/meow/background.py`: launch, worker identity, liveness, and reconciliation.
- Modify `src/meow/cli/core.py`, `src/meow/run_state/core.py`, `src/meow/status_cli/core.py`, `src/meow/cancellation.py`.
- Modify `docs/CLI.md`.
- Test `tests/execution/test_background.py`, `tests/cli/test_cli.py`, `tests/cli/test_status_cli.py`.

### Task 1: Start a supervised worker

**Interface:** `launch_background(repo: Path, argv: list[str]) -> str` creates a run ID and launches `meow _worker RUN_ID` with hidden window on Windows, detached session on POSIX, and run-owned log handles.

- [ ] Add failing tests with a stub worker for immediate run ID return, exclusive launch, worker log path, and launch failure checkpoint. Assert `--background` without `--unattended` is rejected.
- [ ] Run `rtk pytest tests/execution/test_background.py tests/cli/test_cli.py -q` and confirm red.
- [ ] Implement launcher and private worker dispatch; preserve all normal run flags, working directory, and configuration identity. Use `CREATE_NEW_PROCESS_GROUP` plus hidden window on Windows and `start_new_session=True` on POSIX.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: launch unattended background runs`.

### Task 2: Liveness, cancellation, and restart recovery

**Interface:** `inspect_worker(record) -> WorkerState` verifies PID plus start identity/nonce; `reconcile_worker(record)` transitions a missing active worker to interrupted with a recovery instruction.

- [ ] Add failing tests for live worker, stale PID, reused PID, normal completion, crash, cancellation, and restart simulation. Assert no unrelated process is signaled.
- [ ] Run `rtk pytest tests/execution/test_background.py -q` and confirm red.
- [ ] Implement worker identity verification, cooperative cancellation, bounded escalation for a verified owned worker, and startup/status reconciliation. Preserve worktree and logs on abnormal exit.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: recover and cancel background workers`.

### Task 3: Report background operation

**Interface:** `meow status RUN_ID` displays running/interrupted/completed worker state and log path; `meow cancel RUN_ID` uses the same cancellation contract as foreground runs.

- [ ] Add failing CLI tests for active status, clean completion, orphaned worker, and cancellation. Verify `status` remains read-only except explicit reconciliation of stale liveness state.
- [ ] Run `rtk pytest tests/cli/test_status_cli.py tests/cli/test_cli.py -q` and confirm red.
- [ ] Render worker state and document local usage. Notifications remain an optional later adapter; do not couple the worker to an external service.
- [ ] Run focused tests and project lint.
- [ ] Commit: `feat: report background run state`.

### Task 4: Optional completion notification

**Interface:** A configured local notification command receives a sanitized run ID, terminal state, and status command. No notification transport is enabled by default; notification failure never changes the run's verification verdict.

- [ ] Add failing tests for completion, failure, needs-user-action, duplicate terminal events, disabled notifications, and notifier failure. Assert no raw agent output or credential reaches the notifier.
- [ ] Run `rtk pytest tests/execution/test_background.py -q` and confirm red.
- [ ] Add an opt-in notifier adapter triggered once per terminal state and record its outcome separately from run verification. Do not add a general event bus or outbound webhook framework.
- [ ] Run focused tests and project lint.
- [ ] Commit: `feat: notify on background run outcomes`.

## Completion check

Start a fixture background run, close its parent process, inspect it from a new terminal, and cancel a second fixture run. Confirm no terminal prompts, no delivery after cancellation, and recovery instructions after simulated worker loss.

# Cancellation and Worktree Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make active runs cancellable and give users safe worktree inspection, setup, and cleanup by run ID.

**Architecture:** Extend the existing atomic `RunStore` with cancellation intent and worker identity, then supervise only processes the run owns. Add deterministic worktree inspection and guarded cleanup over Git's registered worktree list. Onboarding writes an explicit setup manifest after showing proposed commands/files; new worktrees apply it before agents start.

**Tech Stack:** Python, Git subprocesses, `argparse`, existing `RunStore` and worktree helpers, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Cancellation” and “Worktree lifecycle and setup.” Depends on the permission-policy plan for unattended setup commands.

## Global Constraints

- Cancel never delivers, commits, or pushes after the request is accepted.
- Preserve failed, cancelled, and uncertain worktrees and artifacts.
- Cleanup refuses dirty files, unpushed commits, active runs, wrong repository identity, and paths outside the registered MEOW worktree root.
- Setup copies only explicitly selected regular files; no automatic `.env` or secret discovery/copying.
- `--unattended` never prompts; missing setup input checkpoints and stops.

## Review Focus

- A stale PID reused by an unrelated process must never be killed.
- A cancelled agent after a file edit must resume through review-first logic.
- A symlinked setup source or destination must not escape the project/worktree.
- A worktree with an unpushed commit must survive cleanup even if its files are clean.
- A cancellation arriving during final checks must prevent delivery.

---

## File map

- Create `src/meow/cancellation.py`: cancellation request and active-worker identity.
- Create `src/meow/worktree/controls.py`: inspect/list/clean decisions.
- Create `src/meow/worktree/setup.py`: validated setup manifest execution.
- Modify `src/meow/run_state/core.py`, `src/meow/sprint_runner/core.py`, `src/meow/orchestrator/core.py`, `src/meow/cli/core.py`.
- Modify `skills/onboard/SKILL.md`, `templates/harness.toml.example`, `docs/CLI.md`.
- Test `tests/execution/test_cancellation.py`, `tests/worktrees/test_controls.py`, `tests/worktrees/test_setup.py`, `tests/cli/test_cli.py`.

### Task 1: Cancellation state and active agent stop

**Interface:** `request_cancel(store: RunStore, run_id: str) -> RunRecord`; `cancel_requested(store, run_id) -> bool`. The active worker registers a process identity that can be verified before interruption.

- [ ] Add failing tests for cancellation in planning, mutation, checks, and just before delivery; assert non-success terminal state, preserved worktree, and no delivery call. Test repeated cancel and stale worker identity.
- [ ] Run `rtk pytest tests/execution/test_cancellation.py -q` and confirm red.
- [ ] Implement cancellation intent in the run journal, poll at every phase boundary, and interrupt the active SDK client/process using its supported API. Verify worker identity before any external-process signal. Catch cancellation separately from ordinary failure.
- [ ] Run focused tests and `rtk ruff check` on changed files.
- [ ] Commit: `feat: cancel runs with recoverable state`.

### Task 2: Worktree list, inspect, and guarded clean

**Interface:** `inspect_worktree(repo: Path, run_id: str) -> WorktreeInspection` includes registered path, branch, dirtiness, unpushed commits, and owning run state. `clean_worktree` requires all safe predicates, then calls `git worktree remove` with literal argument vectors.

- [ ] Add failing tests for clean delivered, dirty, unpushed, active, unregistered, detached, and path-escaping worktrees. Assert refusal leaves files intact.
- [ ] Run `rtk pytest tests/worktrees/test_controls.py -q` and confirm red.
- [ ] Implement inspection and cleanup; add `meow worktree list|inspect|clean` to the CLI. Make `list` and `inspect` read-only. Do not implement a force-delete flag.
- [ ] Run focused tests, CLI parser tests, and `rtk ruff check`.
- [ ] Commit: `feat: inspect and safely clean MEOW worktrees`.

### Task 3: Onboarding-approved setup

**Interface:** A validated `[worktree_setup]` section contains exact commands and selected regular-file paths. Onboarding presents the proposed configuration once; worktree creation runs it and records each action/result before planning.

- [ ] Add failing tests for a valid setup, absent source, `..` traversal, symlink, secret-like file, destination collision, command failure, and unattended no-prompt behavior.
- [ ] Run `rtk pytest tests/worktrees/test_setup.py -q` and confirm red.
- [ ] Implement config validation, setup execution, onboarding guidance, and checkpoint reporting. Apply setup after a worktree is registered and before any agent starts; preserve the worktree on failure.
- [ ] Run focused tests, onboarding tests, and `rtk ruff check`.
- [ ] Commit: `feat: configure transparent worktree setup`.

## Completion check

Run all targeted tests and project lint. Manually inspect `meow worktree list` in a fixture repo and verify `cancel` leaves a review-first resume command. Do not test cleanup against a user's real worktree.

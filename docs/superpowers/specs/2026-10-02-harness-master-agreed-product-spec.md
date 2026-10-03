# MEOW harness master: agreed product specification

Date: 2026-10-02  
Status: design for review; no implementation authorized by this document

## Purpose and precedence

MEOW is the user-facing harness for preparing, implementing, verifying, delivering, and recovering development work. The user should normally provide one request through `meow run`; MEOW performs project understanding and design work behind the scenes. The Claude Agent SDK remains responsible for the model loop and tool runtime.

This document records the decisions from the 2026-10-02 feature discussion. Where it conflicts with [the earlier product specification](2026-10-01-harness-master-product-spec.md) or [release sequence](2026-10-01-harness-master-roadmap.md), this document takes precedence. It describes target behavior, not a claim that the current `dev` branch already implements it. Implementation plans must inspect the current code and avoid replacing working facilities.

## User experience and unattended rule

- The normal entry point is `meow run "<request>"`, optionally with `--unattended`. Users do not have to run knowledge, shape, breadboard, or other preparatory commands.
- MEOW chooses the necessary internal phases from the request and repository evidence. Small, clear changes take a short path; complex work receives more analysis.
- Each internal phase leaves inspectable artifacts and a checkpoint. `meow status` explains the current phase, material decisions, evidence, and recovery path without requiring users to manage phase files.
- `--unattended` applies from initial project understanding through shaping, breadboarding, task execution, verification, and delivery. No phase may wait at an interactive prompt. MEOW uses configured defaults and evidence for routine, reversible decisions. When a consequential decision cannot be made safely, it checkpoints the options and reason, stops cleanly, and reports the required human action.
- A stopped, failed, or cancelled run must not silently proceed to delivery. Required gates must pass on the final combined code before delivery.

## Project understanding and design inside a run

### Knowledge base and audit

MEOW reads relevant code, tests, and existing guidance at the start of a run. It builds an evidence-backed context summary for planning and identifies uncertainty that affects the implementation. It should not require a fixed collection of template documents. Internal findings distinguish observed facts, inference, and uncertainty and cite repository evidence.

Routine knowledge gaps do not interrupt a run. Feature runs do not propose, draft, edit, or deliver documentation updates. Onboarding may establish the project knowledge structure with the user's review. The manual `meow docs-update` workflow below owns later documentation maintenance. Deterministic structural checks may validate required paths and links, but they do not generate prose recommendations during feature runs.

### Requirements shaping

MEOW automatically decides when a request needs shaping. For ambiguous or consequential work, it records the problem, desired outcome, constraints, assumptions, viable approaches, fit checks, and chosen approach. It can choose a reversible approach from evidence. A consequential unresolved product choice stops an unattended run with a checkpoint. Clear requests proceed directly to planning. Users are not required to call `meow shape`.

### Breadboarding and design reflection

For complex UI or cross-component work, MEOW maps places, user and code affordances, wiring, error paths, and independently verifiable vertical slices. Reflection identifies concrete gaps such as unwired actions, missing authorization or feedback paths, inconsistent naming, or slices that cannot be tested. The accepted slices inform task order. This phase is skipped for simple work and is not a separate user obligation.

### Plan lifecycle and discoveries

MEOW maintains one canonical plan with automatic draft, in-progress, and complete states. It records material discoveries and explains meaningful deviations in the final summary. Routine implementation details may be revised automatically; a discovery that invalidates a consequential requirement follows the unattended stop rule.

## Execution and verification

### Task dependencies and parallel implementation

Substantial plans may be decomposed into tasks with dependencies, ownership of affected files or interfaces, and verification criteria. MEOW runs independent tasks concurrently only when their boundaries and integration points are clear. It combines results, resolves or reports conflicts without discarding work, and runs review plus required gates against the final combined code. Small changes use a single task. `meow status` shows task progress without making users manage a task board.

### Verification gates and lint

Keep the existing separate lint, test, and build gates. Onboarding discovers the project's real commands, verifies proposed configuration, and makes required versus advisory checks visible. Final required checks run after the last code edit and their command, output, duration, result, and code revision are recorded. A required failure blocks completion and unattended delivery.

Improve the existing lint flow rather than add a universal rule pack: onboarding validates the configured command, per-file feedback reports the remaining findings after fixes, and status makes the final gate result easy to find. Teams own project-specific rules in their existing linter.

### Browser validation

For browser-testable projects, onboarding identifies a usable start command, readiness check, and project browser provider. During a run, MEOW starts the app, runs relevant configured flows, captures failures and useful evidence, and cleans up the app process. A project may require browser validation as a completion gate. Without a verified browser setup, MEOW reports that browser behavior was not checked; it does not invent a pass. MEOW owns this verification stage, not a general browser-control engine.

### Quality concerns

MEOW records specific, evidence-backed maintenance concerns discovered in a run, with location, impact, and suggested follow-up. Later runs can determine whether a concern remains relevant or was resolved. Only material concerns appear in the run summary. MEOW does not produce a single numerical project quality score.

## Run control and operations

### Checkpoints, status, and accounting

Durable checkpoints record phase, attempt, task and review progress, worktree and branch, plan identity, results, failure reason, and recovery instructions. `meow status` defaults to a concise summary and offers detailed evidence. Record elapsed time, role and phase durations, turns, tokens, and cost when reliable SDK data is available; label unavailable values rather than treating them as zero. Record retries and failures. Resume validates repository and worktree identity and reviews possible edits before repeating mutation.

### Permission policy

Projects can configure a small, readable policy governing which roles may use tools, commands, paths, and integrations. MEOW should use SDK enforcement where available instead of building another tool executor. Denials are recorded with reasons. In unattended mode, an action requiring human approval either follows an explicitly configured rule or checkpoints and stops; it never prompts.

### Cancellation

`meow cancel <run-id>` stops the active agent and processes owned by the run, records the interrupted phase, preserves the worktree and evidence, and prevents delivery. Status explains whether and how the run can resume. Cancellation applies to every phase, including knowledge and shaping.

### Worktree lifecycle and setup

MEOW provides list, inspect, and guarded cleanup by run ID. Cleanup checks dirty files, unpushed commits, run state, and ownership; it never silently discards work. An unattended policy may clean only a completed, delivered, clean worktree. Failed, cancelled, or uncertain work remains recoverable.

Onboarding discovers the project's worktree preparation needs and presents the exact proposed commands or selected files for approval once. New worktrees apply that saved setup automatically. Actions and results appear in status and the run summary. No indiscriminate `.env` or secret copying occurs. Missing required inputs stop unattended runs early with a checkpoint. Coordination metadata is added only if parallel task ownership requires it.

### Background execution

After checkpoint, status, cancellation, and recovery are dependable, MEOW will support `meow run ... --unattended --background`. It returns a run ID while a supervised worker continues after the terminal closes. Status and cancel operate on that worker; duplicate starts and machine-restart recovery are handled explicitly. Optional notifications point to completion, failure, or required user action. This is a later implementation dependency, not a replacement for CI or schedulers.

### Editor hooks

Keep existing Claude Code hooks optional. Onboarding explains which hooks were installed and why, and diagnostics can show whether they are active. MEOW runs work without editor hooks, including unattended work. Add another host adapter only for a concrete need.

## Manual documentation maintenance

Normal `meow run`, `plan`, and `--unattended` feature runs make no documentation-drift recommendations and do not automatically update prose documentation. The user manually invokes `meow docs-update` on `dev`, typically once a day. The command examines code and documentation changes since the previous docs update, updates relevant documentation, and presents the diff for review. It must establish and report its comparison baseline and avoid invented claims. The command does not exist in the audited `dev` CLI and is a target feature.

## Integrations and exclusions

- Keep the current role-specific Jira, GitLab, and tester MCP approach. Add a new integration through that pattern when there is a concrete service need. Do not build a general MCP registry now.
- Do not bundle Context7, a browser-control CLI, or a universal lint-rule pack.
- Do not rebuild the SDK's model loop, typed tool dispatch, context compression, parallel tool scheduler, or provider cache. MEOW persists workflow facts; the SDK manages agent runtime concerns.
- Do not add general file snapshots, agent-session forking, persistent teammate mailboxes, autonomous task claiming, or automatic secret copying as default features.
- Run events and external hooks are deferred to [Future features](../../FUTURE_FEATURES.md). Do not implement a general event bus in this scope.

## Implementation sequence and acceptance

1. Strengthen the run-control foundation: enforceable permissions, reliable usage accounting, cancellation, checkpoints, and guarded worktree cleanup.
2. Integrate automatic knowledge gathering, shaping, breadboarding, plan discoveries, and onboarding-configured worktree setup into the single-run path, including every unattended branch.
3. Add task dependency execution and final combined-code verification; complete browser verification and lint/onboarding refinements.
4. Add the independent manual `meow docs-update` command. Add MEOW-managed background execution only after its run-control dependencies are sound.

Acceptance for each increment requires that a clear request retain a direct path, an unattended run never wait for input, required verification use the final code revision, interruptions preserve recoverable evidence, and the user can understand material decisions through status and the final summary. No feature is considered complete solely because its CLI parser or artifact schema exists; behavior and failure paths must be verified.

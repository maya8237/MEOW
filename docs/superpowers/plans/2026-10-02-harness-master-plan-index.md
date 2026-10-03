# MEOW Harness Master Implementation Plan Index

Date: 2026-10-02  
Status: complete plan set for review; implementation has not started

Source: [agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md). This index and the linked plans supersede the 2026-10-01 harness-master release plans wherever their behavior differs from the agreed spec. They extend the current `dev` code rather than recreate the features already implemented there.

## Execution order

1. [Run usage accounting](2026-10-02-run-usage-accounting.md): SDK result capture, journal persistence, status totals.
2. [Agent permission policy](2026-10-02-agent-permission-policy.md): enforceable role limits and unattended denial behavior. Can be developed independently of usage accounting, then integrated before unattended pre-plan work.
3. [Cancellation and worktree control](2026-10-02-cancellation-worktrees.md): cancel, worktree inspection/cleanup, onboarding-approved setup. Depends on permission policy for unattended setup commands.
4. [Automatic project understanding](2026-10-02-automatic-project-understanding.md): internal knowledge, shaping, breadboarding, plan lifecycle, and selective onboarding knowledge structure. Depends on permission and cancellation behavior across all phases.
5. [Verification and onboarding](2026-10-02-verification-onboarding.md): preflighted project checks, clearer lint feedback, and browser validation. May start after permission policy; final integration follows the combined-code task path.
6. [Parallel task execution](2026-10-02-parallel-task-execution.md): bounded task graph, isolated workers, integration, whole-branch review/gates. Depends on worktree, permission, cancellation, and final checks.
7. [Evidence-backed quality concerns](2026-10-02-quality-concerns.md): advisory, deduplicated maintenance findings. Depends on stable review/run evidence.
8. [Manual docs update](2026-10-02-manual-docs-update.md): `meow docs-update` on `dev`, isolated from feature runs. Can be implemented independently once command authority and final diff review are verified.
9. [Editor hook visibility](2026-10-02-editor-hook-visibility.md): onboarding explanation and read-only diagnostics for existing optional Claude Code hooks. Independent of feature-run phases.
10. [Background runs](2026-10-02-background-runs.md): detached unattended worker, status, cancellation, recovery, and optional notification. Last because it depends on reliable run control.

## Cross-plan acceptance

- A user can submit one request through `meow run`; knowledge, shaping, and breadboarding happen only when justified and require no preparatory command.
- `--unattended` never waits for input in any phase. An unresolved consequential decision records a checkpoint and stops before mutation or delivery.
- Normal feature runs make no documentation-update recommendations or edits. `meow docs-update` is manual, restricted to `dev`, and shows a reviewable diff.
- Required lint, test, build, and configured browser gates assess the final combined code revision.
- Cancellation and failed integration preserve worktrees and evidence; cleanup never silently discards work.
- Existing Jira, GitLab, tester, lint, run-state, and hook facilities remain the integration baseline.

## Explicitly outside this plan set

The [future-events note](../../FUTURE_FEATURES.md) remains deferred. A general MCP registry, bundled Context7, universal lint rule pack, browser-control engine, agent runtime replacement, provider cache control, session forking, persistent teammate mailbox, and automatic secret copying are not target features. No plan silently introduces them.

## Plan execution convention

Each linked plan is independently reviewable and testable. Before implementation, inspect the current `dev` branch and any local changes; update a plan when code has moved or a planned interface conflicts with an existing one. Execute feature work through the MEOW harness as directed by `AGENTS.md`, or obtain explicit approval for an alternative isolated workflow. Do not start a dependent plan until its prerequisites have passing tests and a reviewed integration state.

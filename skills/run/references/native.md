# Native run

Read [the shared protocol](../../_shared/native-mode.md) first.

## Native mode

Named workflows (including `quick`) and `--force-disable-tests` use
[CLI mode](../SKILL.md#cli-mode); preserve the supplied flags.
Before fetching an issue, preparing a worktree, or creating a checkpoint, run
`meow native verify --work-dir "<project-path>"`. If `--test` was requested
or `tester.configured` is true, use [CLI mode](../SKILL.md#cli-mode): native mode
cannot run the tester's server/agent lifecycle. CLI selects it by workflow and risk.
Preserve the user's Jira, existing-plan, source-branch, and worktree options.
For a saved native run, inspect and continue that same ID with `meow resume <run_id>
--continue --repair`; for an existing plan without a saved run use the documented
`--resume-at review --plan <plan_file>` flow, never a fresh sprint.

1. **Request text**: whatever the user gave, unless they named a Jira issue
   (or asked for "the latest issue") -- then skip to step 1a instead.
   Otherwise if there's no text and no issue either, ask for a one-line
   description. The project uses `.meow/config.toml` at its root (with an
   optional ignored `.meow/config.local.toml`; see
   the shared protocol if it does not).
1a. **Jira-sourced build**: the project's `.meow/config.toml` must have
   `[jira]` with `project_key` (`branch_prefix` optional, default
   `issue/`). The `[jira.mcp]` table is **not** used here: fetch through
   the Jira MCP tools already connected to this session (tool names
   containing `jira`). If none connected, or `[jira]` missing, say so and
   stop; point at docs/INTEGRATIONS.md in the meow repo. Fetch the named
   issue, or the most recently created one in `project_key`; need `key`,
   `summary`, `description` -- stop and report if any is missing. Request
   text becomes `Resolve Jira issue <key>: <summary>` + blank line +
   `<description>`. Feature name: `issue-<key>` lowercased with anything
   outside `A-Za-z0-9._-` turned into `-`. Branch:
   `<branch_prefix><KEY>`. Run `meow native prepare --name "<feature>"
   --branch "<branch>" --work-dir "<project-path>"` instead of step 2's
   `prepare` call, then perform step 2's checkpoint and continue at step 3 -- do not pass
   `--worktree` when dispatching reviewers in this flow (CLI parity). On
   PASS (step 5), finalize commits and pushes the branch; report one line:
   `{"issue": "<key>", "branch":
   "<branch>"}` instead of step 5's normal report.
2. Pick a safe feature name (slugify the request, e.g. `add-dark-mode`). Run
   `meow native prepare --name "<name>" --fresh --work-dir "<project-path>"`.
   `--fresh` gives a new run its own worktree (`<name>-2`, ... if the name is
   taken) and the JSON `name` is the one used -- take `plan_file` and
   `review_file` from the JSON, never rebuild them from your own name.
   **Resuming:** if the user asks to continue, resume, pick up, or build on
   existing code, an earlier run, a worktree, or a plan -- in any wording --
   leave `--fresh` off so the existing `.worktrees/<name>` is reused (use the
   name they gave, or the one matching `.worktrees/`; ask only if several
   fit), then follow "Resume check" below.
   Add `--no-worktree` if the user wants the main repo instead of an isolated
   worktree (then `--name` may be omitted). `--from <branch>` creates a fresh
   worktree and rejects in-place execution. `prepare` also onboards a new project; if its
   `onboarding.files` is non-empty, tell the user which files it set up. On a
   nonzero exit (e.g. uncommitted changes), report the message verbatim and
   stop. Keep `active_dir`, `plan_file`, `review_file`,
   `max_rounds`, `use_worktree` from the JSON; do all further work in `active_dir`.
   Immediately call `meow native checkpoint preparing --request "<request>"
   --work-dir "<project-path>" --active-dir "<active_dir>"` and keep its
   `run_id`. Reuse `--run-id <run_id>` for every later checkpoint.
   Pass `--target BRANCH` on this first checkpoint when explicitly requested;
   otherwise it saves the configured target. On resume, use the journal's
   MR target shown by `meow status <run_id>`, never recompute it.
   **Resume check:** run `meow native latest-plan --work-dir
   "<project-path>" --active-dir "<active_dir>"`. If it returns a plan and that
   file has a numbered task list and a `## Sprint Contract`, the plan is ready:
   use it, write a `planned` checkpoint with `--plan <plan_file>`, skip step 3
   and go to step 5, reviewing the existing code first. If there is no plan
   or it is incomplete, say so and plan normally in step 3 (still without
   `--fresh`, in the same worktree).
3. Plan (skip if the user supplied an existing plan file, or the resume check
   found a ready plan): `meow native round <plan_file> --reset --active-dir <active_dir>`,
   then checkpoint `planning --run-id <run_id>` with both directory flags.
   As planner, write `plan_file` per the shared protocol's Planner row.
   Read it back and confirm it has a numbered task list and a `## Sprint Contract`.
   Write a `planned` checkpoint with `--plan <plan_file>`.
4. If the user asked to approve the plan first, show it and ask before
   generating; if declined, stop without implementing anything.
5. Run the review loop from the shared protocol: generator first (or, when
   resuming at review, review the existing code first and generate only if it
   fails). Pass `--worktree` to `prompt reviewer-plan` when `use_worktree` is
   true (never for the Jira-sourced flow -- see step 1a).
   Before a generator turn, write `generator_started` with `--round N`; after
   it returns, write `generator_finished`. If it stops unexpectedly, write
   `interrupted_mutation` and inspect the worktree before another edit.
   Record `reviewer_started` before dispatch and `reviewer_finished
   --review <review_file> --reviewer PASS|FAIL` after reading the verdict.
   If tester configuration is enabled after preparation, continue the saved
   run with `meow resume <run_id> --continue --repair` through CLI mode; native
   checkpoints have no Claude session IDs, so `--repair` starts fresh agent
   sessions against the saved plan. Do not invent a tester verdict.
   On final PASS, call `meow native finalize <run_id>
   --work-dir "<project-path>" --active-dir "<active_dir>"`; report success
   only when its JSON says `"complete": true`.
6. Report: on PASS, the plan file and review file paths (or, for a
   Jira-sourced build, step 1a's JSON report instead); when a round comes
   back `exhausted`, say the sprint did not pass after `max_rounds` rounds and
   point at the review file for the last feedback. A successful finalize in a
   separate worktree commits and pushes its branch.
   State the saved MR target; `--from` only chooses the starting branch.
   For worktree delivery, say the loop finished and final delivery is being
   verified, then open the MR/PR into that target and report the
   pipeline status. MEOW's pipeline-fix loop is CLI-only; never claim
   "Verified by pipeline" unless the pipeline actually passed.


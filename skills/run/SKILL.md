---
name: run
description: Run a full meow sprint for a feature request in the current project — plan it, implement it, then review and fix it in a loop until it passes. Optionally sourced from a Jira issue instead of typed text, built in a retained branch. Uses the CLI by default so automatic project understanding and run checkpoints apply.
---

# run

Plan -> implement -> review, in a loop, for one feature request -- either
typed directly, or fetched from a Jira issue. CLI mode is the default: `meow
run` performs knowledge gathering, shaping, optional breadboarding, planning,
implementation, verification, and review within one checkpointed run. Use the
CLI mode section below for ordinary requests. Native mode is an explicit opt-in
when the user asks for implementation inside this Claude Code session. For
native mode, read [`../_shared/native-mode.md`](../_shared/native-mode.md)
first; it defines the `meow native` helper and review loop.

## Native mode

1. **Request text**: whatever the user gave, unless they named a Jira issue
   (or asked for "the latest issue") -- then skip to step 1a instead.
   Otherwise if there's no text and no issue either, ask for a one-line
   description. The project uses `.meow/config.toml` at its root (with an
   optional ignored `.meow/config.local.toml`; legacy `.harness.toml` is a
   fallback; see
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
   --branch "<branch>" --working-dir "<project-path>"` instead of step 2's
   `prepare` call, then perform step 2's checkpoint and continue at step 3 -- do not pass
   `--worktree` when dispatching reviewers in this flow (CLI parity). On
   PASS (step 5), finalize commits and pushes the branch; report one line:
   `{"issue": "<key>", "branch":
   "<branch>"}` instead of step 5's normal report.
2. Pick a safe feature name (slugify the request, e.g. `add-csv-export`). Run
   `meow native prepare --name "<name>" --working-dir "<project-path>"`.
   Add `--no-worktree` if the user wants the main repo instead of an isolated
   worktree (then `--name` may be omitted); add `--source-branch <branch>` if
   they named one. On a nonzero exit (e.g. uncommitted changes), report the
   message verbatim and stop. Keep `active_dir`, `plan_file`, `review_file`,
   `max_rounds`, `use_worktree` from the JSON; do all further work in `active_dir`.
   Immediately call `meow native checkpoint preparing --request "<request>"
   --working-dir "<project-path>" --active-dir "<active_dir>"` and keep its
   `run_id`. Reuse `--run-id <run_id>` for every later checkpoint.
3. Plan (skip if the user supplied an existing plan file, or asked to resume at
   review): `meow native round <plan_file> --reset --active-dir <active_dir>`,
   then, as planner, write `plan_file` per the shared protocol's Planner row.
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
   Record an enabled tester verdict separately with `tester_finished
   --tester PASS|FAIL`. On final PASS, call `meow native finalize <run_id>
   --working-dir "<project-path>" --active-dir "<active_dir>"`; report success
   only when its JSON says `"complete": true`.
6. Report: on PASS, the plan file and review file paths (or, for a
   Jira-sourced build, step 1a's JSON report instead); when a round comes
   back `exhausted`, say the sprint did not pass after `max_rounds` rounds and
   point at the review file for the last feedback. A successful finalize in a
   separate worktree commits and pushes its branch.

## CLI mode

Use CLI mode for all ordinary runs so MEOW owns the complete automatic pre-plan
flow and its checkpoints. When the user requests `--test`, pass it through to
`meow run` (including Jira and `--resume-at review` flows); configured servers
stay alive throughout tester validation.

Runs `meow run` (separate Agent SDK sessions, works headless). From the project root:

```bash
meow run "<feature request>" --name "<generated-feature-name>" --working-dir "<project-path>"
meow run --jira [ISSUE-KEY] --working-dir "<project-path>"   # Jira-sourced, retained branch
meow run "<feature request>" --name "<generated-feature-name>" --test --working-dir "<project-path>"
```

Add `--no-worktree` to operate in the main repo (name then optional; not
applicable to `--jira`, which always uses its own pushable-branch worktree).
If `meow` isn't on PATH, tell the user to install it (README: `pip install -e .`
in a venv, or `pipx install -e .`). Stream its progress (`[planner]`, `[generator]`,
`[reviewer]`) to the user. Report the plan and review file paths on success
(or, for `--jira`, the printed `{"issue": ..., "branch": ...}` JSON line), or
that it failed after `max_rounds` and where the last review is.


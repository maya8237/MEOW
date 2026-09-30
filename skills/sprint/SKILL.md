---
name: sprint
description: Run a full meow sprint for a feature request in the current project — plan it, implement it, then review and fix it in a loop until it passes. Runs natively in this Claude Code session by default. Use when the user wants meow to build a feature end to end.
---

# sprint

Plan -> implement -> review, in a loop, for one feature request. By default
this runs **natively**: you plan and implement, and a fresh reviewer subagent
grades each round. Read [`../_shared/native-mode.md`](../_shared/native-mode.md)
(relative to this skill's base directory) first — it defines the `meow native`
helper, the roles, lint discipline, round limits and the review loop that the
steps below refer to. Use **CLI mode** (bottom) only if the user explicitly asks
for the headless/separate-process run.

## Native mode

1. The feature request is whatever text the user gave. If none, ask for a
   one-line description first. The project must have a `.harness.toml` at its
   root (see the shared protocol if it does not).
2. Pick a safe feature name (slugify the request, e.g. `add-csv-export`). Run
   `meow native prepare --name "<name>" --working-dir "<project-path>"`.
   Add `--no-worktree` if the user wants the main repo instead of an isolated
   worktree (then `--name` may be omitted); add `--source-branch <branch>` if
   they named one. On a nonzero exit (e.g. uncommitted changes), report the
   message verbatim and stop. Keep `active_dir`, `plan_file`, `review_file`,
   `max_rounds`, `use_worktree` from the JSON; do all further work in `active_dir`.
3. Plan (skip if the user supplied an existing plan file, or asked to resume at
   review): `meow native round <plan_file> --reset --active-dir <active_dir>`,
   then, as planner, write `plan_file` per the shared protocol's Planner row.
   Read it back and confirm it has a numbered task list and a `## Sprint Contract`.
4. If the user asked to approve the plan first, show it and ask before
   generating; if declined, stop without implementing anything.
5. Run the review loop from the shared protocol: generator first (or, when
   resuming at review, review the existing code first and generate only if it
   fails). Pass `--worktree` to `prompt reviewer-plan` when `use_worktree` is true.
6. Report: on PASS, the plan file and review file paths; when a round comes
   back `exhausted`, say the sprint did not pass after `max_rounds` rounds and
   point at the review file for the last feedback.

## CLI mode

Runs `meow run` (separate Agent SDK sessions, works headless). From the project root:

```bash
meow run "<feature request>" --name "<generated-feature-name>" --working-dir "<project-path>"
```

Add `--no-worktree` to operate in the main repo (name then optional). If `meow`
isn't on PATH, tell the user to install it (README: `pip install -e .` in a venv,
or `pipx install -e .`). Stream its progress (`[planner]`, `[generator]`,
`[reviewer]`) to the user. Report the plan and review file paths on success, or
that it failed after `max_rounds` and where the last review is.

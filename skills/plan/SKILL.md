---
name: plan
description: Use when the user wants a MEOW sprint plan and testable Sprint Contract before implementation. Defaults to native planning.
---

# plan

Writes a numbered task list and a Sprint Contract, implements nothing. Runs
**natively** by default: you are the planner. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) first for helper and planner rules.
Use **CLI mode** (bottom) only if the user explicitly asks for it.

## Native mode

1. The feature request is whatever text the user gave. If none, ask for a
   one-line description. The project uses `.meow/config.toml` at its root;
   onboarding can create it for a new project.
2. Slugify a feature name (e.g. `add-csv-export`) and run
   `meow native prepare --name "<name>" --fresh --allow-dirty --work-dir "<project-path>"`
   (`meow plan` never required a clean tree). `--fresh` never reuses an
   existing worktree: a taken name becomes `<name>-2`, ... -- use the JSON
   `name`, `plan_file` and `active_dir` it returns, not your own name. Add `--no-worktree` if the user
   wants the main repo (name then optional), `--from <branch>` if given.
   Report failures verbatim and stop.
3. Create a `preparing` checkpoint with `--request "<feature request>"`,
   `--work-dir "<project-path>"`, and `--active-dir "<active_dir>"`; retain its
   `run_id`. Record `planning --run-id <run_id>` before planning with the same
   directory flags.
   As planner, write `plan_file` (inside `active_dir`) following the shared
   protocol's Planner row. Use an explorer subagent for any codebase research
   you don't need in full. Read the file back and confirm the task list and
   `## Sprint Contract` exist. Record `planned --run-id <run_id> --plan
   <plan_file>` with the same directory flags. Write no application code.
4. Report the plan file's path and the worktree name (JSON `name`). If they
   want it built: `/meow:run` (full loop) -- tell it to continue that worktree
   and plan, which is a resume, so it will not create a new worktree -- or, once
   code exists, `/meow:review`.

## CLI mode

```bash
meow plan "<feature request>" --name "<generated-feature-name>" --work-dir "<project-path>"
```

Add `--no-worktree` to use the main repo instead of an isolated worktree. Report the plan file's path.

---
name: plan
description: Have meow write a sprint plan (with a testable Sprint Contract) for a feature request, without implementing it. Runs natively in this Claude Code session by default. Use when the user wants a plan to review before any code gets written.
---

# plan

Writes a numbered task list and a Sprint Contract, implements nothing. Runs
**natively** by default: you are the planner. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) first for the `meow native` helper and planner rules.
Use **CLI mode** (bottom) only if the user explicitly asks for it.

## Native mode

1. The feature request is whatever text the user gave. If none, ask for a
   one-line description. The project uses `.meow/config.toml` at its root;
   onboarding can create it for a new project.
2. Slugify a feature name (e.g. `add-csv-export`) and run
   `meow native prepare --name "<name>" --allow-dirty --working-dir "<project-path>"`
   (`meow plan` never required a clean tree). Add `--no-worktree` if the user
   wants the main repo (name then optional), `--source-branch <branch>` if given.
   Report failures verbatim and stop.
3. As planner, write `plan_file` (inside `active_dir`) following the shared
   protocol's Planner row. Use an explorer subagent for any codebase research
   you don't need in full. Read the file back and confirm the task list and
   `## Sprint Contract` exist. Write no application code.
4. Report the plan file's path. If they want it built: `/meow:run` (full loop)
   or, once code exists, `/meow:review`.

## CLI mode

```bash
meow plan "<feature request>" --name "<generated-feature-name>" --working-dir "<project-path>"
```

Add `--no-worktree` to use the main repo instead of an isolated worktree. If
`meow` isn't on PATH, tell the user to install it (README: `pip install -e .`
in a venv, or `pipx install -e .`). Report the plan file's path.


---
name: meow-review
description: Run meow's reviewer against an already-implemented sprint plan and fix every problem it finds, looping until it passes. Runs natively in this Claude Code session by default. Use when code exists for a plan and needs to be graded and cleaned up, not planned or built from scratch.
---

# meow-review

Grades the current code against an existing plan's Sprint Contract; on FAIL,
fixes the findings and re-reviews until it passes or `max_rounds` runs out.
Runs **natively** by default: a fresh reviewer subagent grades, you fix. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) first. Use **CLI mode** (bottom) only if explicitly asked.

## Native mode

1. The project needs a `.harness.toml` at its root.
2. Find the plan: the file the user named, else
   `meow native latest-plan --working-dir "<project-path>"`. If none exists, tell
   the user to run `/meow:meow-plan` or `/meow:sprint` first; do not invent one.
3. Get limits: `meow native prepare --no-worktree --allow-dirty --working-dir "<project-path>"`
   (read `max_rounds`; it changes nothing). Then start the counter with
   `meow native round <plan_file> --reset`.
4. Review-first loop (shared protocol): `round` (this is round 1) -> reviewer
   subagent (`prompt reviewer-plan --plan <plan_file>`, no `--worktree`) -> `verdict`.
   PASS ends. On FAIL, each further round is: `round` (stop if exhausted) ->
   fix the findings as generator (verify each finding first; report unsupported
   or out-of-scope ones) -> project-wide lint -> fresh reviewer -> `verdict`.
5. Report PASS, or, if exhausted, the review file's path so the user can read
   the remaining feedback.

## CLI mode

```bash
meow review --plan-file "<path>" --working-dir "<project-path>"
```

Omit `--plan-file` to review the newest plan in `docs_dir`. If `meow` isn't on
PATH, tell the user to install it (README: `pip install -e .` in a venv, or
`pipx install -e .`). If no plan exists, point at `/meow:meow-plan`. Stream its
`[reviewer]`/`[generator]` progress; report PASS or the review file path.

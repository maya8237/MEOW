---
name: meow-plan
description: Have meow write a sprint plan (with a testable Sprint Contract) for a feature request, without implementing it. Use when the user wants a plan to review before any code gets written.
---

# meow-plan

Runs the meow harness's planner only (`meow plan`) against the current
project — writes a numbered task list and a Sprint Contract, but implements
nothing.

1. The feature request is whatever text the user gave when invoking this
   skill. If none was given, ask for a one-line feature description first.
2. The current project must have a `.harness.toml` at its root. If it's
   missing, tell the user and stop; point them at meow's `GUIDE.md` for
   onboarding rather than guessing at lint commands.
3. Run, from the project root:

   ```bash
   meow plan "<feature request>"
   ```

   If `meow` isn't found on PATH, tell the user to install meow first
   (its README: a venv with `pip install -e .`, or `pipx install -e .` for a
   global command) — don't guess at a path to some venv.
4. Report the plan file's path back to the user. If they want it
   implemented, that's `/meow:sprint` (full loop) or `/meow:meow-review`
   once code already exists against this plan.

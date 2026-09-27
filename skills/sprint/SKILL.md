---
name: sprint
description: Run a full meow sprint for a feature request in the current project — plan it, implement it, then review and fix it in a loop until it passes. Use when the user wants meow to build a feature end to end.
---

# sprint

Runs the meow harness's full plan -> implement -> review loop (`meow run`)
against the current project.

1. The feature request is whatever text the user gave when invoking this
   skill. If none was given, ask for a one-line feature description first.
2. The current project must have a `.harness.toml` at its root — this is the
   project meow will harness, not necessarily the meow repo itself. If it's
   missing, tell the user and stop; point them at meow's `GUIDE.md` for
   onboarding rather than guessing at lint commands.
3. Run, from the project root:

   ```bash
   meow run "<feature request>"
   ```

   If `meow` isn't found on PATH, tell the user to install meow first
   (its README: a venv with `pip install -e .`, or `pipx install -e .` for a
   global command) — don't guess at a path to some venv.
4. This can take several agent rounds and prints progress as it goes
   (`[planner]`, `[generator]`, `[reviewer]` lines) — stream that output to
   the user rather than waiting silently for it to finish.
5. Report the final outcome: on success, the plan file and final review file
   paths; on failure after `max_rounds`, say so and point at the review file
   for the last recorded feedback.

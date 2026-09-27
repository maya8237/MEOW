---
name: meow-review
description: Run meow's reviewer against an already-implemented sprint plan and fix every problem it finds, looping until it passes. Use when code exists for a plan and needs to be graded and cleaned up, not planned or built from scratch.
---

# meow-review

Runs the meow harness's reviewer (`meow review`) against an existing
sprint plan in the current project. The reviewer grades the current code
against that plan's Sprint Contract; on FAIL, meow loops the generator
against the feedback and re-reviews, until it passes or `max_rounds` runs
out.

1. The current project must have a `.harness.toml` at its root. If it's
   missing, tell the user and stop; point them at meow's `GUIDE.md` for
   onboarding rather than guessing at lint commands.
   `meow review` accepts `--working-dir PATH` (also `--work-dir` or `-d`) to
   select the project whose plan and implementation should be reviewed.
2. If the user named a specific plan file, run:

   ```bash
   meow review --plan-file "<path>" --working-dir "<project-path>"
   ```

   Otherwise run `meow review --working-dir "<project-path>"` — it reviews
   the most recently modified plan in that project's `docs_dir`.

   If `meow` isn't found on PATH, tell the user to install meow first
   (its README: a venv with `pip install -e .`, or `pipx install -e .` for a
   global command) — don't guess at a path to some venv. If it fails because
   no plan file exists yet, tell the user to run `/meow:meow-plan` or
   `/meow:sprint` first rather than inventing one.
3. This can take several rounds and prints progress as it goes (`[reviewer]`,
   `[generator]` lines) — stream that output to the user rather than waiting
   silently for it to finish.
4. Report the final outcome: PASS (nothing left to fix), or the review file's
   path if it still fails after `max_rounds` so the user can inspect the
   remaining feedback themselves.

---
name: review-fix-review
description: Fix an existing review's findings and re-review, looping until it passes (up to max_rounds). Use when a review already exists (from meow review, cr, or gitlab-review) and the findings need fixing and re-checking, not a fresh review from scratch.
---

# review-fix-review

Runs `meow review-fix-review` against the current project: starts from an
already-written review verdict file, fixes what it found, and re-reviews,
looping generator/fixer <-> reviewer the same way `meow review` does, up
to `max_rounds`.

1. The current project must have a `.harness.toml` at its root. If it's
   missing, tell the user and stop; point them at meow's `GUIDE.md` for
   onboarding rather than guessing at lint commands.
   `meow review-fix-review` accepts `--working-dir PATH` (also
   `--work-dir` or `-d`) to select the project whose review file should be
   fixed.
2. The prompt is whatever text the user gave when invoking this skill,
   describing what the re-review should focus on or check for -- this is
   required, not optional. If none was given, ask for one before running
   anything.
3. If the user named a specific review file, run:

   ```bash
   meow review-fix-review "<prompt>" --review-file "<path>" --working-dir "<project-path>"
   ```

   Otherwise omit `--review-file` -- it picks the most recently modified
   review file (from `meow review`, `cr`, or `gitlab-review`) in that
   project's `docs_dir` on its own.

   If `meow` isn't found on PATH, tell the user to install it first (this
   repo's README: a venv with `pip install -e .`, or `pipx install -e .`
   for a global command) -- don't guess at a path to some venv.
4. A review file from `meow gitlab-review` (a GitLab merge request review)
   cannot be fixed this way -- there is no local checkout of the merge
   request's code for a generator to edit. `meow review-fix-review` reports
   this clearly and exits; if it happens, tell the user to check out the
   MR's branch locally and use `meow review`/`meow cr` against that
   checkout instead, or address the MR feedback directly.
5. This can take several rounds and prints progress as it goes
   (`[reviewer]`, `[generator]`/`[review_fixer]` lines) -- stream that
   output to the user rather than waiting silently for it to finish.
6. Report the final outcome: PASS (nothing left to fix), or the review
   file's path if it still fails after `max_rounds` so the user can inspect
   the remaining feedback themselves.

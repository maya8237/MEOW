---
name: review-fix-review
description: Fix an existing review's findings and re-review, looping until it passes (up to max_rounds). Runs natively in this Claude Code session by default. Use when a review already exists (from meow review, cr, or gitlab-review) and the findings need fixing and re-checking, not a fresh review from scratch.
---

# review-fix-review

Starts from an already-written review file, fixes what it found, and re-reviews,
looping up to `max_rounds`. Runs **natively** by default: you fix, a fresh
reviewer subagent re-grades. Read [`../_shared/native-mode.md`](../_shared/native-mode.md)
(relative to this skill's base directory) first. Use **CLI mode** (bottom) only
if explicitly asked.

## Native mode

1. The prompt is whatever text the user gave — **required**. If none, ask for
   one before doing anything. The project needs a `.harness.toml` at its root.
2. Pick the review file: the one the user named, else
   `meow native latest-review --working-dir "<project-path>"` (gives `review_file`
   and `flavor`).
3. `flavor: gitlab` cannot be fixed here (no local checkout of the MR). Tell the
   user to check out the MR branch and use `/meow:meow-review` / `/meow:meow-cr`
   against it, or address the feedback directly, and stop.
4. Read the review file (`meow native verdict <file>`). If it already says PASS,
   report that and stop. Get `max_rounds` from
   `meow native prepare --no-worktree --allow-dirty` (changes nothing).
5. Anchor the counter: plan flavor -> the plan file (the review file's name
   without `-review`); prompt flavor -> the review file itself. Run
   `meow native round <anchor> --reset`, then `round <anchor>` once: the
   existing review is round 1.
6. Each further round: `round <anchor>` (stop if exhausted) -> fix the findings
   -> project-wide lint -> fresh reviewer -> `verdict`.
   - **Plan flavor**: fix as the plan's generator; review with
     `prompt reviewer-plan --plan <plan> --focus "<prompt>"` (the focus stays on
     every round).
   - **Prompt flavor**: fix as a scoped fixer (`meow native prompt review-fixer`;
     smallest edit per finding, no scope creep); review with
     `prompt reviewer-prompt --focus "<prompt>"`.
7. Report PASS, or if exhausted the review file's path for the remaining feedback.

## CLI mode

```bash
meow review-fix-review "<prompt>" --review-file "<path>" --working-dir "<project-path>"
```

Omit `--review-file` to use the newest review in `docs_dir`. A GitLab MR review
is rejected by the command with an explanation. If `meow` isn't on PATH, tell the
user to install it (README: `pip install -e .` in a venv, or `pipx install -e .`).
Stream `[reviewer]`/`[generator]`/`[review_fixer]` progress; report PASS or the
review file path.

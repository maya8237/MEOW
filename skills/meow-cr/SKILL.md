---
name: meow-cr
description: Run meow's reviewer against a free-text prompt instead of a sprint plan, grading whatever is currently in the working tree against what the prompt asked for. Use when there's no plan file to check against, or the user wants a quick review of what changed against a stated intent.
---

# meow-cr

Runs the meow harness's reviewer (`meow cr`) against a plain-text prompt in
the current project, instead of a Sprint Contract in a plan file. The
reviewer runs `git status`/`git diff` to see what actually changed, checks
it against the prompt's requirements, and reports PASS/FAIL with evidence.
Unlike `/meow:meow-review`, this never loops a generator to fix issues — it
only reports.

1. The prompt is whatever text the user gave when invoking this skill. If
   none was given, ask what feature/change to review against first.
2. The current project must have a `.harness.toml` at its root. If it's
   missing, tell the user and stop; point them at meow's `GUIDE.md` for
   onboarding rather than guessing at lint commands.
3. By default, keep the same isolated-worktree policy as the other harness
   commands: run from the project root with a named worktree when the user is
   doing feature work in a sandboxed checkout.

   ```bash
   meow cr "<prompt>" --worktree "<generated-worktree-name>"
   ```

   If the user did not provide a prompt, run:

   ```bash
   meow cr --worktree "<generated-worktree-name>"
   ```

   In that case, the reviewer uses the current working tree's `git status`
   and `git diff` as the review input itself, without needing a textual
   prompt. If the user explicitly wants the repo root instead of an isolated
   worktree, add `--no-worktree` instead.

   If `meow` isn't found on PATH, tell the user to install meow first
   (its README: a venv with `pip install -e .`, or `pipx install -e .` for a
   global command) — don't guess at a path to some venv.
4. This prints progress as it goes (`[orchestrator]`, `[lint]` lines) --
   stream that output to the user rather than waiting silently for it to
   finish.
5. Report the final outcome: PASS or FAIL, plus the review file's path so
   the user can inspect the full evidence themselves.

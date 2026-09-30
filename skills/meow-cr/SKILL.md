---
name: meow-cr
description: Run meow's reviewer against a free-text prompt instead of a sprint plan, grading whatever is currently in the working tree against what the prompt asked for. Runs natively in this Claude Code session by default. Use when there's no plan file to check against, or the user wants a quick review of what changed against a stated intent.
---

# meow-cr

One reviewer pass over the working tree, graded against the user's prompt (or
against `git diff` when there is no prompt). Reports PASS/FAIL only; it never
loops a generator. Runs **natively** by default: a fresh reviewer subagent does
the grading. Read [`../_shared/native-mode.md`](../_shared/native-mode.md)
(relative to this skill's base directory) first. Use **CLI mode** (bottom) only
if explicitly asked.

## Native mode

1. The prompt is whatever text the user gave; it may be empty (then the diff is
   the review basis). The project needs a `.harness.toml` at its root.
2. Run `meow native prompt reviewer-prompt --focus "<prompt>" --working-dir "<project-path>"`
   (omit `--focus` when there is no prompt). Dispatch one reviewer subagent per
   the shared protocol; it writes `review.md` (the JSON's `review_file`).
3. Run `meow native lint --working-dir "<project-path>"` first if you want the
   lint result in hand; the reviewer also runs the gate commands itself.
4. `meow native verdict <review_file>`; report PASS or FAIL, the summary, and
   the review file path. Do not edit any code.

## CLI mode

```bash
meow cr "<prompt>" --working-dir "<project-path>"
```

With no prompt run `meow cr --working-dir "<project-path>"`, which reviews the
git diff. If `meow` isn't on PATH, tell the user to install it (README:
`pip install -e .` in a venv, or `pipx install -e .`). Stream its progress and
report PASS/FAIL plus the review file path.

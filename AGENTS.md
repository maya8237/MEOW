# AGENTS.md

MEOW — Management, Execution & Optimization of Workflows.

## Harness

Feature work in this repo runs through the MEOW harness engine — which is this
repo — not ad hoc editing. From the repo root, with `.venv` active, provide a
worktree name (or explicitly opt into working in the repo root):

```bash
meow run "<feature description>" --worktree "<feature-name>"
```

`meow plan "<feature description>" --worktree "<feature-name>"` writes just the sprint plan, without
implementing it. `meow review [--plan-file PATH]` re-runs the reviewer
against an already-implemented plan (the most recent one in `docs_dir` by
default) and loops fixes back through the generator until it passes. Pass a
specific plan with `--plan-file PATH` (or `--plan PATH`).

Config lives in `.harness.toml`. Sprint plans, contracts, and reviews are
written to `docs/exec-plans/active/`.

To onboard a *different* repo onto meow, see [GUIDE.md](GUIDE.md) — it's
written for a Claude session working in that other repo, and covers what's
strictly required versus merely recommended, generic to any language.

## Claude Code plugin

This repo is also a Claude Code plugin (`.claude-plugin/plugin.json` +
`skills/`), so the same three operations are available as skills when meow
is installed as a plugin in a project: `/meow:sprint`, `/meow:meow-plan`, and
`/meow:meow-review`. Each is a thin wrapper that shells out to the `meow`
CLI above — see `skills/*/SKILL.md` for what each one runs.

## Lint

`ruff check` is the gate, declared as the single `[[lint]]` entry in
`.harness.toml`. The harness appends `--fix` when linting individual files the
generator touches, and runs `ruff check` unmodified project-wide as part of the
reviewer's verdict. Rule selection lives in `pyproject.toml`.

A project can declare any number of `[[lint]]` commands (see GUIDE.md for the
`per_file`/`gate` fields); meow itself only needs one.

## Layout

The engine is split by responsibility under `src/meow/`: `config.py`
(`.harness.toml` loading and the lint-command model), `sprint.py` (the shared
per-sprint state), `lint.py` (the auto-fixing per-file hook), `agents/`
(`explorer.py`, `planner.py`, `generator.py`, and `reviewer.py`, one module per
agent role), `orchestrator.py` (the generator <-> reviewer round loop), and
`cli.py` (the `meow` console-script entry point). The `src/` layout is
deliberate: code run from the repo root reaches the *installed* copy, so a
broken editable install is caught rather than masked.

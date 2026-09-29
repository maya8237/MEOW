# AGENTS.md

MEOW — Management, Execution & Optimization of Workflows.

## Harness

Feature work in this repo runs through the MEOW harness engine — which is this
repo — not ad hoc editing. From the repo root, with `.venv` active, provide a
feature name (or explicitly opt into working in the repo root):

```bash
meow run "<feature description>" --name "<feature-name>"
```

`meow plan "<feature description>" --name "<feature-name>"` writes just the sprint plan, without
implementing it. `meow review [--plan-file PATH]` re-runs the reviewer
against an already-implemented plan (the most recent one in `docs_dir` by
default) and loops fixes back through the generator until it passes. Pass a
specific plan with `--plan-file PATH` (or `--plan PATH`).
Every command accepts `--working-dir PATH` (also `--work-dir` or `-d`) to
select the project directory.

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
per-sprint state and `build_sprint`, which wires it up from config), `lint.py`
(the auto-fixing per-file hook plus reporting the configured lint plan),
`worktree.py` (git/filesystem bootstrapping: `.gitignore` upkeep and creating
per-feature worktrees), `agents/` (one module per agent role, plus a shared
base), `orchestrator.py` (the generator <-> reviewer round loop and the
top-level `run_*` entry points `cli.py` dispatches into), and `cli.py` (the
`meow` console-script entry point). The `src/` layout is deliberate: code run
from the repo root reaches the *installed* copy, so a broken editable install
is caught rather than masked.

## Agent structure

Every role in `agents/` follows the same class-based shape: `*Agent(context)`,
where `context` satisfies the `AgentContext` protocol in `agents/base.py`
(model selection per role, an active working directory, and lint commands).
The generator additionally needs the pre-wired explorer definition and lint
hook, declared on the narrower `GeneratorContext` protocol rather than on
every role's context. The shared `Agent` base builds `ClaudeAgentOptions` and
runs one-shot SDK queries consistently across roles; `Sprint` (sprint-workflow
state) and the generic, sprint-free `ProjectContext` (config + a directory,
used by `cr`) both satisfy `AgentContext` (`Sprint` also satisfies
`GeneratorContext`), so a role class works the same way whether or not a
sprint is in play. The explorer stays declarative — `ExplorerAgent.definition()` returns
an `AgentDefinition` because the SDK consumes it as a nested subagent — while
the generator keeps its own persistent `ClaudeSDKClient`/context-manager
lifecycle so a session survives across feedback rounds. Each module also keeps
thin, function-shaped compatibility wrappers (`make_explorer_agent`,
`run_planner`, `run_reviewer`, `run_prompt_reviewer`, `Generator`) around its
class for existing callers and imports.

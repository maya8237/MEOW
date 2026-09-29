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
`meow issue [ISSUE-KEY]` fetches a Jira issue and solves it end to end in a
pushed worktree branch (needs `[jira]`/`[jira.mcp]` in `.harness.toml`).
`meow gitlab-review "<mr-url>"` fetches a GitLab merge request's diff and
grades it, reporting PASS/FAIL without editing anything (needs
`[gitlab.mcp]`). See [GUIDE.md](GUIDE.md) for both config sections.
`meow lint-fix` runs every configured `[[lint]]` command and fixes what it
finds; `meow lint-fix --report-only` only runs and reports, fixing nothing
-- that's the mode the `lint-fix` skill uses, doing the fixing itself.
`run`/`plan --source-branch BRANCH` (also `--from`/`-b`) checks a freshly
created worktree out from that branch instead of the main checkout's
current HEAD. `run` skips the uncommitted-changes check only when both a
worktree is being created (not `--no-worktree`) and `--source-branch` was
given for it; every other combination keeps the check as before.
`run`/`issue --manually-approve-plan` (also `-m`) prints the plan after the
planner writes it and prompts on stdin before the generator implements it;
declining exits non-zero without running the generator. `plan` doesn't take
it -- it never implements what it plans. Never pass it on a scheduled
`meow issue` run: nothing is attached to answer the prompt, so it hangs.
`run --resume-at {generate,review}` (default `generate`) picks up an
interrupted sprint at the review stage instead of re-running the
generator, using `--plan-file` if given or the latest plan in `docs_dir`
otherwise; `plan` and `issue` don't take it.
Every command accepts `--working-dir PATH` (also `--work-dir` or `-d`) to
select the project directory.

Config lives in `.harness.toml`. Sprint plans, contracts, and reviews are
written to `docs/exec-plans/active/`.

To onboard a *different* repo onto meow, see [GUIDE.md](GUIDE.md) — it's
written for a Claude session working in that other repo, and covers what's
strictly required versus merely recommended, generic to any language.

## Claude Code plugin

This repo is also a Claude Code plugin (`.claude-plugin/plugin.json` +
`skills/`), so the same operations are available as skills when meow is
installed as a plugin in a project: `/meow:sprint`, `/meow:meow-plan`,
`/meow:meow-review`, `/meow:meow-issue`, `/meow:gitlab-review`, and
`/meow:lint-fix`. Each is a thin wrapper that shells out to the `meow` CLI
above — see `skills/*/SKILL.md` for what each one runs. `lint-fix` is the
odd one out: it runs `meow lint-fix --report-only` and then does the fixing
itself in the calling session, rather than having meow spin up its own
agent the way every other skill here does.

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
(the auto-fixing per-file hook, reporting the configured lint plan, and the
project-wide fix/check functions `lint_fix.py` uses), `worktree.py`
(git/filesystem bootstrapping: `.gitignore` upkeep and creating per-feature
worktrees), `agents/` (one module per agent role, plus a shared base),
`orchestrator.py` (the generator <-> reviewer round loop and the top-level
`run_*` entry points `cli.py` dispatches into), `issue_solver.py`,
`gitlab_reviewer.py`, and `lint_fix.py` (the `run_*` entry points for
`issue`/`gitlab-review`/`lint-fix`, each its own module rather than folded
into `orchestrator.py`, the same SOLID/SRP separation the reviewer itself
checks for), and `cli.py` (the `meow` console-script entry point). The
`src/` layout is deliberate: code run from the repo root reaches the
*installed* copy, so a broken editable install is caught rather than masked.

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

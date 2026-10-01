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
implementing it.
`meow run --jira [ISSUE-KEY]` fetches a Jira issue and solves it end to end
in a pushed worktree branch instead of a typed request (needs
`[jira]`/`[jira.mcp]` in `.harness.toml`); `--name`/`--no-worktree`/
`--source-branch`/`--resume-at`/`--plan-file` are rejected alongside it --
it always builds its own worktree and branch.
`meow run --lint-fix` runs every configured `[[lint]]` command and fixes
what it finds; `meow run --lint-fix --report-only` only runs and reports,
fixing nothing -- that's the mode the `lint-fix` skill uses, doing the
fixing itself. It takes no request text, no `--jira`, and no worktree flags.
`run`/`plan --source-branch BRANCH` (also `--from`/`-b`) checks a freshly
created worktree out from that branch instead of the main checkout's
current HEAD. `run` skips the uncommitted-changes check only when both a
worktree is being created (not `--no-worktree`) and `--source-branch` was
given for it; every other combination keeps the check as before.
`run`/`run --jira --manually-approve-plan` (also `-m`) prints the plan after
the planner writes it and prompts on stdin before the generator implements
it; declining exits non-zero without running the generator. `plan` doesn't
take it -- it never implements what it plans. Never pass it on a scheduled
`meow run --jira` run: nothing is attached to answer the prompt, so it hangs.
`run --resume-at {generate,review}` (default `generate`) picks up an
interrupted sprint at the review stage instead of re-running the
generator, using `--plan-file` if given or the latest plan in `docs_dir`
otherwise; `plan` and `run --jira`/`--lint-fix` don't take it.

`meow review [PROMPT] [--fix] [--jira [KEY] | --gitlab MR-LINK | --branch
BRANCH --target TARGET | --plan-file PATH | --review-file PATH]` is its own
subcommand covering every review-and-optionally-fix operation -- report-only
by default (single pass, PASS/FAIL, nothing edited), `--fix` loops
review-fix-review up to `max_rounds` and raises if it never passes. Exactly
one source may be given; none given auto-discovers the latest plan in
`docs_dir`, falling back to a free-text/diff review if none exists (the old
bare `meow review` and `meow cr` defaults, combined). `--gitlab` fetches a
GitLab merge request's diff (needs `[gitlab.mcp]`) and is always read-only --
combining it with `--fix` is rejected, there's no local checkout to fix.
`--jira` fetches a Jira issue (needs `[jira]`/`[jira.mcp]`) and reviews the
current code against it. `--branch`/`--target` reviews a local branch's diff
against a target (plain `git diff`, no GitLab MCP needed); it's the only
source that creates its own worktree (isolated by default, `--no-worktree`
to fix in place on an already-checked-out branch). `--plan-file` reviews a
specific plan's implementation against its Sprint Contract. `--review-file`
resumes fixing an already-written review file instead of running a fresh
review, auto-detecting its flavor (a GitLab or branch review file can't be
resumed this way -- re-run with `--gitlab`/`--branch` instead). See
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for the `[jira]`/`[gitlab.mcp]`
config sections.

Every command accepts `--working-dir PATH` (also `--work-dir` or `-d`) to
select the project directory.

Config lives in `.harness.toml`. Sprint plans, contracts, and reviews are
written to `docs/exec-plans/active/`.

To onboard a different repo onto meow, use the `/meow:onboard` Claude Code
skill. It inspects the project, configures its harness, and asks which optional
integrations or project-specific features to set up using yes/no/later choices.
Integration configuration details live in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Native (in-session) skill execution

Each skill runs in one of two modes. **Native** (default when invoked from
Claude Code): the calling session plans/generates, a fresh Task subagent
reviews each round, and `meow native <prepare|latest-plan|latest-review|
verdict|lint|round|prompt|push>` supplies the deterministic facts as JSON
(no agents started). **CLI**: the skill shells out to `meow <command>` and
the Agent SDK does the work; this path is unchanged and is what runs
headless. The shared native protocol is `skills/_shared/native-mode.md`;
role prompts live once in `src/meow/prompts.py` and feed both the SDK
agents and `meow native prompt`, so change a prompt there, never in a
skill. Both modes must keep the same `.harness.toml` semantics, file names
and `SUMMARY:`/`STATUS:` verdict format. Design:
`docs/superpowers/specs/2026-09-29-native-skill-execution-design.md`.

## Claude Code plugin

This repo is also a Claude Code plugin (`.claude-plugin/plugin.json` +
`skills/`), so the same operations are available as skills when meow is
installed as a plugin in a project: `/meow:sprint` (plain build, or
`--jira`-sourced), `/meow:plan`, `/meow:review` (every source), and
`/meow:lint`. Each is a thin wrapper that shells out to the `meow` CLI
above — see `skills/*/SKILL.md` for what each one runs. `lint` is the
odd one out: it runs `meow run --lint-fix --report-only` and then does the
fixing itself in the calling session, rather than having meow spin up its
own agent the way every other skill here does.

## Lint

`ruff check` is the gate, declared as the single `[[lint]]` entry in
`.harness.toml`. The harness appends `--fix` when linting individual files the
generator touches, and runs `ruff check` unmodified project-wide as part of the
reviewer's verdict. Rule selection lives in `pyproject.toml`.

A project can declare any number of `[[lint]]` commands; see the annotated
example in `templates/harness.toml.example` for the `per_file`/`gate` fields.
MEOW itself only needs one.

## Layout

The engine is split by responsibility under `src/meow/`: `config.py`
(`.harness.toml` loading, the lint-command model, and failing fast on a
lint command that's a script or shell only usable on a different OS than
the one meow is running on), `sprint.py` (the shared
per-sprint state and `build_sprint`, which wires it up from config), `lint.py`
(the auto-fixing per-file hook, reporting the configured lint plan, and the
project-wide fix/check functions `lint_fix.py` uses), `worktree.py`
(git/filesystem bootstrapping: `.gitignore` upkeep and creating per-feature/
existing-branch worktrees), `rules.py` (reads `docs/RULES.md` and injects it
into role system prompts), `logging.py` (structured, structlog-based logging
setup shared by every module), `prompts.py` (role prompts as pure functions
of plain values, shared by both the SDK agents and native-mode execution),
`plan_files.py` (plan/review file naming and lookup within a project's
`docs_dir`), `agents/` (one module per agent role, plus a shared base),
`orchestrator.py` (just the shared generator<->reviewer round-loop engine
now: `_prepare_sprint` and the three round-loop shapes -- `_run_rounds`,
`_run_review_rounds`, `_run_prompt_fix_rounds`), `sprint_runner.py` (the
`run_sprint`/`run_plan` top-level flows `cli.py` dispatches `meow run`/`meow
plan` into), `review_cli.py` (the `run_review_command` dispatcher for `meow
review`'s every source -- prompt/`--jira`/`--gitlab`/`--branch`/`--plan-file`/
`--review-file`, report-only or `--fix`), `issue_solver.py` (`meow run
--jira`'s fetch/worktree+branch/sprint/push flow), `gitlab_reviewer.py`
(GitLab MR config/fetch helpers `review_cli.py` uses), `branch_reviewer.py`
(the branch-name sanitizing helper `review_cli.py` uses), and `lint_fix.py`
(the `run_lint_fix` entry point for `meow run --lint-fix`) -- the
deterministic helpers behind native, in-Claude-Code-session execution --
`native_prepare.py` (worktree/clean-tree bootstrapping and plan/review
lookup), `native_lint.py` (lint execution), `native_state.py` (the on-disk
round counter), and `native_prompt.py` (prompt/agent-wiring construction),
each its own module for the same reason -- plus `native.py` (a thin façade
that re-exports their public names under one `from meow import native`
import) and `native_cli.py` (the `meow native ...` argparse/JSON wrapper
around it), and `cli.py` (the `meow` console-script entry point: the `run`/
`review`/`plan`/`native` subparsers and their dispatch). The `src/` layout is
deliberate: code run from the repo root reaches the *installed* copy, so a
broken editable install is caught rather than masked.

## Agent structure

Every role in `agents/` follows the same class-based shape: `*Agent(context)`,
where `context` satisfies the `AgentContext` protocol in `agents/base.py`
(model selection per role, an active working directory, and lint commands).
The generator additionally needs the pre-wired explorer definition and lint
hook, declared on the narrower `GeneratorContext` protocol rather than on
every role's context. The shared `Agent` base builds `ClaudeAgentOptions` and
runs one-shot SDK queries consistently across roles; `Sprint` (sprint-workflow
state) and the generic, sprint-free `ProjectContext` (config + a directory,
used by `meow review`) both satisfy `AgentContext` (`Sprint` also satisfies
`GeneratorContext`), so a role class works the same way whether or not a
sprint is in play. The explorer stays declarative — `ExplorerAgent.definition()` returns
an `AgentDefinition` because the SDK consumes it as a nested subagent — while
the generator keeps its own persistent `ClaudeSDKClient`/context-manager
lifecycle so a session survives across feedback rounds. Each module also keeps
thin, function-shaped compatibility wrappers (`make_explorer_agent`,
`run_planner`, `run_reviewer`, `run_prompt_reviewer`, `Generator`) around its
class for existing callers and imports.

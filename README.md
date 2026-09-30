# MEOW

[![CI](https://github.com/maya8237/MEOW/actions/workflows/ci.yml/badge.svg)](https://github.com/maya8237/MEOW/actions/workflows/ci.yml)

MEOW — Management, Execution & Optimization of Workflows.

The harness engine: a planner/generator/reviewer loop built on the
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

`meow` is *generic* — nothing about any particular project is baked into it.
Four agents (`explorer`, `planner`, `generator`, `reviewer`) are coordinated by
plain Python control flow: the planner writes a sprint plan with a testable
Sprint Contract, the generator implements it under an auto-fixing lint hook, and
a skeptical reviewer grades the result PASS/FAIL. FAIL feeds back into the
generator, up to `max_rounds` times.

Every project-specific value — the lint commands, the per-role models, the round
cap, where sprint files land — is read at runtime from a `.harness.toml` file in
the *target project's* root. This repo holds only the engine.

## Agent structure

Each of the four roles is a `*Agent` class in `src/meow/agents/` built on a
shared `Agent` base (`src/meow/agents/base.py`), which constructs SDK options
and runs one-shot queries the same way for every role. Role classes take a
generic `AgentContext` — either `Sprint` (sprint workflow state) or the
sprint-free `ProjectContext` (project config + a directory, used by `cr`) —
so review operations don't need a sprint or plan file. The explorer stays
declarative (`AgentDefinition`, since the SDK runs it as a nested subagent);
the generator keeps a persistent `ClaudeSDKClient` so a session survives
across feedback rounds.

## Agent skills

MEOW makes role-specific Superpowers skills available through the Claude Agent
SDK:

- **Explorer:** `superpowers:systematic-debugging` guides evidence gathering
  when investigating a bug. The explorer remains read-only.
- **Planner:** `superpowers:writing-plans` guides task sizing, file mapping,
  testability, and plan self-review. MEOW still controls the plan path and
  Sprint Contract format.
- **Generator:** `superpowers:executing-plans` guides task-by-task work;
  `superpowers:test-driven-development` guides code changes;
  `superpowers:systematic-debugging` guides failure investigation;
  `superpowers:receiving-code-review` guides how it checks reviewer findings;
  and `superpowers:verification-before-completion` guides its completion
  evidence. MEOW retains worktree setup and the review loop.
- **Reviewer:** `superpowers:verification-before-completion` guides independent
  checks of the implementation and command evidence before it writes its
  verdict.

## Crash retry and error surfacing

The underlying `claude` CLI subprocess the Agent SDK shells out to can crash
outright (an abnormal termination, not a normal nonzero exit) -- most often
seen on Windows as a fast-fail/NTSTATUS exit code such as `0xC0000409`, or on
POSIX as being killed by a signal. `Agent.run_query` (the one-shot query path
every role but the generator/review-fixer/lint-fixer uses, including every
`ReviewerAgent` review) retries up to 3 attempts total with a short backoff
when this happens -- `query()` is documented as stateless/one-shot, so
retrying by starting a fresh query is safe, with nothing to corrupt or
duplicate. A non-crash SDK failure (the CLI itself reporting an error) is
never retried, but every failure -- crashed or not -- raises a `RuntimeError`
naming the role, the exit code, the attempt count, and any captured stderr,
instead of a bare exit code. The reviewer's own direct git calls
(`git status`/`git diff`/`git merge-base`, used to build review context) get
the same crash-retry treatment. The persistent, multi-turn agents (generator,
review-fixer, lint-fixer) are deliberately not covered -- retrying mid-session
there risks losing conversation state or duplicating edits, a different and
harder problem than retrying a stateless one-shot query.

## Project rules

A project can add a `docs/RULES.md` file to shape every role's behavior
directly, not just what the reviewer catches afterward. Content before
the first role heading applies to all four roles (explorer, planner,
generator, reviewer); a `## Reviewer` / `## Planner` / `## Generator` /
`## Explorer` heading (case-insensitive) scopes everything under it to
just that role. For example:

```markdown
Follow this project's existing code style and commit conventions.

## Reviewer
When testing UI changes, use Chrome DevTools to exercise edge cases
-- empty states, long text, disabled controls -- not just the happy
path.
```

No `docs/RULES.md` is the default; meow's behavior is unaffected until a
project adds one.

## Install

Into a dedicated virtualenv, from this repo's root:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e .
```

(`.venv/bin/python` on macOS/Linux.) The install is editable, so edits to the
engine take effect immediately with no reinstall. Verify with
`.venv/Scripts/meow --help`.

Because the venv is local to this repo, `meow` is *not* on your global PATH.
From another project's root, either activate the venv first or call the script
by its full path:

```bash
/path/to/meow/.venv/Scripts/meow run "Add CSV export" --name "add-csv-export"
```

If you'd rather have `meow` available everywhere without activating anything,
`pipx install -e .` gives it its own environment but a global shim.

## Run

From the root of a project that has a `.harness.toml` (with the venv active,
or via the full path shown above):

```bash
meow run "Add CSV export" --name "add-csv-export"
```

Every subcommand accepts `--working-dir PATH` (also `--work-dir` or `-d`) to
select a project outside the current directory. The sprint plan and review
land in that project's `docs_dir`, never in this repo.

Two narrower subcommands are also available:

```bash
meow plan "Add CSV export" --name "add-csv-export" --working-dir PATH # write the sprint plan only
meow review --working-dir .worktrees/add-csv-export                  # review the latest plan in that worktree
meow review --plan-file PATH --working-dir .worktrees/add-csv-export # review a specific plan there
```

Use a worktree by default to isolate feature work from the main repo. `--no-worktree`
lets `run` and `plan` intentionally run in the selected working directory instead;
the feature name is optional in that mode. `meow review` (below) always uses the
selected working directory and does not take a feature name, except its `--branch`
source, which creates its own isolated worktree by default.

`--source-branch BRANCH` (also `--from` or `-b`) checks a freshly created worktree
out from that branch instead of the main checkout's current HEAD:

```bash
meow run "Backport the fix" --name "backport-fix" --source-branch "release/1.0"
```

The uncommitted-changes check is skipped only when *both* hold: a worktree is
being created for this invocation (worktree mode, not `--no-worktree`) *and*
`--source-branch` was given for it -- the worktree is then built from that
branch, not the main checkout's current state, so the main checkout's own
uncommitted changes are irrelevant to it. Every other combination -- no
worktree, or a worktree with no source branch -- keeps the check exactly as
before. Reusing an existing worktree (by name) also ignores `--source-branch`
-- resuming what's already there takes priority over re-branching it.

`--manually-approve-plan` (also `-m`) pauses `run` (and `issue`, below)
between planning and implementation: after the planner writes the plan,
meow prints it and asks for approval before the generator starts. Declining
exits with a nonzero status and no generator run:

```bash
meow run "Add CSV export" --name "add-csv-export" --manually-approve-plan
```

`meow plan` doesn't take this flag -- it never implements what it plans, so
there's nothing after planning to gate.

`--resume-at {generate,review}` picks up an interrupted sprint without
starting over. `generate` (default) is today's behavior: plan fresh unless
`--plan-file` is given, then the generator goes first. `review` skips
planning -- using `--plan-file` if given, or auto-detecting the latest
plan in `docs_dir` otherwise -- and reviews the existing code first, only
running the generator if that review finds something to fix:

```bash
meow run "Add CSV export" --name "add-csv-export" --resume-at review
```

Use this when a prior `run` was interrupted after the generator already
produced code (or partway through addressing reviewer feedback) --
resuming at `review` re-evaluates whatever is actually on disk rather than
re-running the generator on code that's already there. `meow plan` doesn't
take this flag either, for the same reason it doesn't take
`--manually-approve-plan`: it never implements what it plans.

### `meow run --jira`

Fetches a Jira issue and solves it end to end in a pushed worktree branch —
built for unattended, scheduled use:

```bash
meow run --jira PROJ-123     # a specific issue
meow run --jira               # the most recently created issue in [jira].project_key
```

Requires `[jira]`/`[jira.mcp]` in `.harness.toml` and a reachable Jira MCP
server (checked before anything else runs). Always uses its own worktree, on
branch `<branch_prefix><ISSUE-KEY>` (default `issue/<ISSUE-KEY>`), which it
pushes to `origin` once the sprint passes. On success it prints one JSON
line — `{"issue": "PROJ-123", "branch": "issue/PROJ-123"}` — and exits 0.
`--name`/`--no-worktree`/`--source-branch`/`--resume-at`/`--plan-file` are
rejected alongside `--jira` — it always builds its own worktree and branch
and never resumes an existing plan. See
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for the config fields and for
running it from a Windows Scheduled Task with persistent logging
(`MEOW_LOG_FILE`).

`run --jira` also accepts `--manually-approve-plan`/`-m`, with the same
plan-then-approve-then-implement behavior as plain `run`. Don't combine it
with a scheduled/unattended run, though — there's no console attached to
answer the prompt, so it will hang waiting for an approval that never comes.

### `meow run --lint-fix`

Runs every command configured under `[[lint]]` and either fixes what's
found or just reports it, depending on the mode:

```bash
meow run --lint-fix                    # apply each command's --fix, then fix what's left with an agent
meow run --lint-fix --report-only      # only run the commands and print their raw output; fix nothing
```

The default mode actually edits the project: an auto-fix pass runs each
command's own fix flag project-wide, then whatever is still failing is
handed to a dedicated fixer agent in a loop (reusing the same per-file
auto-fixing hook `run`'s generator uses) until clean or `max_rounds` is
reached — same clean-tree requirement as plain `run`, since it edits the
selected working directory in place with no worktree isolation.
`--report-only` never edits or fixes anything and never runs an agent; it
exists for the `lint-fix` skill, which runs it this way and does the fixing
itself in the calling Claude Code session instead.

## `meow review`

One command covers every review-and-optionally-fix operation, sourced from a
free-text prompt, a Jira issue, a GitLab merge request, a local branch's diff
against a target, or an existing plan file (auto-discovered if you give
nothing):

```bash
meow review                                             # latest plan in docs_dir, or the git diff if none exists
meow review "Check error handling on the API boundary"  # review against a free-text prompt
meow review --jira PROJ-123                              # review the current code against a Jira issue
meow review --gitlab "https://gitlab.example.com/group/project/-/merge_requests/123"
meow review --branch "feature/add-csv-export" --target main
meow review --plan-file docs/exec-plans/active/add-csv-export.md
```

Every one of these is **report-only** by default: a single reviewer pass,
graded PASS/FAIL, nothing edited. Add `--fix` to loop review→fix→review up to
`max_rounds` instead, raising if it never passes:

```bash
meow review --fix "Check error handling on the API boundary"
meow review --fix --branch "feature/add-csv-export" --target main
```

`--gitlab` is always read-only — there's no local checkout of a merge
request to fix, so combining it with `--fix` is rejected. Every other source
can be fixed. `--branch` is the only source that creates its own worktree —
isolated by default (`--target` is required, never guessed, since
main/dev/master varies by project; reuses the worktree on a repeated run
against the same branch), or `--no-worktree` to fix in place on whatever's
already checked out, which must already be that branch, or it fails with a
clear error. Every other source reviews the selected `--working-dir` as-is,
no worktree involved. Writes its verdict to `docs_dir` (`review.md`,
`gitlab-review.md`, `branch-review.md`, or `<plan>-review.md`, matching the
source).

`--review-file PATH` (also `-r`) resumes fixing an existing review file
instead of running a fresh initial review, auto-detecting its flavor — a
plan-based file resumes against its Sprint Contract; a prompt-based one
fixes against the given prompt/basis directly (the positional argument
becomes that prompt when combined with `--review-file`). A GitLab or branch
review file can't be resumed this way — neither has a target/checkout left
to re-diff against — re-run `meow review --gitlab`/`--branch` instead, which
already loop to `max_rounds` on their own:

```bash
meow review --review-file docs/add-csv-export-review.md "Check error handling on the API boundary"
```

`meow review` needs `[gitlab.mcp]`/`[jira]`+`[jira.mcp]` in `.harness.toml`
only when you actually use `--gitlab`/`--jira` — see
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for those config fields. It
never requires a clean working tree — each source's own guards (the
`--branch` worktree/checkout checks) are its concern instead.

## Claude Code plugin

This repo doubles as a Claude Code plugin: add it as a plugin source and these
skills become available in any project that also has a `.harness.toml`:

| Skill | CLI equivalent (CLI mode) |
|---|---|
| `/meow:sprint "<feature>"` | `meow run "<feature>"` |
| `/meow:sprint --jira [ISSUE-KEY]` | `meow run --jira [ISSUE-KEY]` |
| `/meow:meow-plan "<feature>"` | `meow plan "<feature>"` |
| `/meow:review [...]` | `meow review [...]` — every source (prompt/`--jira`/`--gitlab`/`--branch`/`--plan-file`/`--review-file`), see "`meow review`" above |
| `/meow:lint-fix` | `meow run --lint-fix --report-only`, then the skill fixes what it reports |

### Native mode vs CLI mode

Every skill has two execution modes. By default a skill runs **natively**, in
the Claude Code session that invoked it: that session is the planner and
generator (using the same superpowers skills the SDK roles use), and each
review round is a fresh subagent, so nothing runs in a separate, invisible
Agent SDK process. Jira and GitLab are read through the MCP tools already
connected to the session rather than through `[jira.mcp]`/`[gitlab.mcp]`.
Ask for "CLI mode" (or headless) and the skill shells out to `meow <command>`
instead, exactly as before.

The `meow` CLI itself is unchanged and stays the way to run anything
unattended (terminal, scheduled task). Both modes read the same
`.harness.toml` and write the same files (`<name>.md`, `<name>-review.md`,
`review.md`, `gitlab-review.md`, same `SUMMARY:`/`STATUS:` verdict format), use
the same worktree rules and `max_rounds`, so a run begun in one mode can be
continued in the other.

Native mode needs `meow` installed only for `meow native ...`, an agent-free
helper that prints JSON for the mechanical parts: startup guards and worktree
resolution (`prepare`), plan/review lookup (`latest-plan`, `latest-review`),
verdict parsing (`verdict`), lint runs (`lint`), an on-disk round counter that
enforces `max_rounds` across context compaction (`round`), pushing an issue
branch (`push`), and the exact role prompts the SDK agents use (`prompt`, from
the shared `src/meow/prompts.py`). The loop protocol lives in
`skills/_shared/native-mode.md`. Per-edit lint feedback in native mode is a
protocol step (`meow native lint --file`) rather than an SDK hook.

## Onboarding a project

Copy a template from `templates/` to the target repo's root as
`.harness.toml`: `harness.toml.example` (annotated, language-neutral),
`harness.toml.python.example`, or `harness.toml.typescript.example`.

See [GUIDE.md](GUIDE.md) for the field reference, the recommended `docs/`
layout, and a setup checklist — it's written for a Claude session onboarding a
different repo onto meow, generic to any language.
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) covers the optional Jira/GitLab
config, running `meow run --jira` on a schedule, and a full error-message reference.

## Layout

```
meow/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── GUIDE.md                             # onboarding a *different* repo onto meow
├── .claude-plugin/
│   └── plugin.json                      # Claude Code plugin manifest
├── skills/
│   ├── _shared/native-mode.md           # protocol every skill follows in native mode
│   ├── sprint/SKILL.md                  # /meow:sprint -> harness run (plain or --jira)
│   ├── meow-plan/SKILL.md               # /meow:meow-plan -> harness plan
│   ├── review/SKILL.md                  # /meow:review -> harness review (every source)
│   └── lint-fix/SKILL.md                # /meow:lint-fix -> harness run --lint-fix --report-only
├── docs/
│   ├── INTEGRATIONS.md                  # Jira/GitLab config, scheduling, error reference
│   └── exec-plans/
│       └── active/                      # meow harnessing itself writes here
├── templates/
│   ├── harness.toml.example             # annotated, language-neutral
│   ├── harness.toml.python.example      # ruff (gate) + mypy (non-blocking)
│   └── harness.toml.typescript.example  # eslint (gate) + tsc (non-blocking)
└── src/
    └── meow/
        ├── __init__.py
        ├── config.py                   # .harness.toml loading + lint-command model
        ├── sprint.py                   # per-sprint state shared by every role
        ├── lint.py                     # auto-fixing per-file hook + project-wide fix/check
        ├── worktree.py                 # git/filesystem bootstrapping + per-feature/existing-branch worktrees
        ├── rules.py                    # docs/RULES.md parsing, injected into role prompts
        ├── logging.py                  # structured logging setup shared by every module
        ├── plan_files.py               # plan/review file naming + lookup in docs_dir
        ├── agents/
        │   ├── base.py                 # shared AgentContext/Agent base + ProjectContext
        │   ├── explorer.py             # explorer agent definition
        │   ├── planner.py              # planner agent
        │   ├── generator.py            # generator agent
        │   ├── reviewer.py             # reviewer agents and review helpers
        │   ├── issue_fetcher.py        # Jira MCP preflight + issue fetch, for `meow run --jira`
        │   ├── gitlab_fetcher.py       # GitLab MCP preflight + MR fetch, for `meow review --gitlab`
        │   ├── lint_fixer.py           # lint-fix agent, for standalone `meow run --lint-fix`
        │   └── review_fixer.py         # review-fix agent, for prompt-based `meow review --fix`
        ├── orchestrator.py             # shared generator <-> reviewer round-loop engine only
        ├── sprint_runner.py            # `meow run`/`meow plan` top-level flows
        ├── review_cli.py               # `meow review` dispatcher: every source, report-only or --fix
        ├── issue_solver.py             # `meow run --jira` flow: fetch, worktree+branch, sprint, push
        ├── gitlab_reviewer.py          # GitLab MR fetch/config helpers, used by review_cli.py
        ├── branch_reviewer.py          # branch-name sanitizing helper, used by review_cli.py
        ├── lint_fix.py                 # `meow run --lint-fix` flow: fix-or-report, standalone vs. skill
        ├── prompts.py                  # role prompts as pure functions, shared by both modes
        ├── native_prepare.py           # native mode: worktree bootstrapping + plan/review lookup
        ├── native_lint.py              # native mode: lint execution
        ├── native_state.py             # native mode: on-disk round counter
        ├── native_prompt.py            # native mode: prompt/agent-wiring construction
        ├── native.py                   # native mode: re-exports the above under one import
        ├── native_cli.py               # `meow native` argparse wiring + JSON output
        └── cli.py                      # `meow` console-script entry point
```

# MEOW

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
the feature name is optional in that mode. `review` and `cr` always use the selected
working directory and do not take a feature name. `review` still runs the reviewer first; if it already
passes, nothing else runs. On FAIL it loops the generator against the feedback and
re-reviews, same as `run`, up to `max_rounds`.

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

### `meow issue`

Fetches a Jira issue and solves it end to end in a pushed worktree branch —
built for unattended, scheduled use:

```bash
meow issue PROJ-123          # a specific issue
meow issue                   # the most recently created issue in [jira].project_key
```

Requires `[jira]`/`[jira.mcp]` in `.harness.toml` and a reachable Jira MCP
server (checked before anything else runs). Always uses its own worktree, on
branch `<branch_prefix><ISSUE-KEY>` (default `issue/<ISSUE-KEY>`), which it
pushes to `origin` once the sprint passes. On success it prints one JSON
line — `{"issue": "PROJ-123", "branch": "issue/PROJ-123"}` — and exits 0.
See [GUIDE.md](GUIDE.md) for the config fields and for running it from a
Windows Scheduled Task with persistent logging (`MEOW_LOG_FILE`).

### `meow gitlab-review`

Fetches a GitLab merge request's diff and grades it, reporting a PASS/FAIL
verdict — read-only, like `cr`, and never loops a generator to fix issues:

```bash
meow gitlab-review "https://gitlab.example.com/group/project/-/merge_requests/123"
```

Requires `[gitlab.mcp]` in `.harness.toml` and a reachable GitLab MCP server
(checked before anything else runs). Unlike `meow issue`, it never creates a
worktree, edits code, or pushes anything — it only fetches the merge
request's title, description, and diff, grades them, and writes the verdict
to `gitlab-review.md` in the selected working directory's `docs_dir`. See
[GUIDE.md](GUIDE.md) for the config fields.

### `meow lint-fix`

Runs every command configured under `[[lint]]` and either fixes what's
found or just reports it, depending on the mode:

```bash
meow lint-fix                          # apply each command's --fix, then fix what's left with an agent
meow lint-fix --report-only            # only run the commands and print their raw output; fix nothing
```

The default mode actually edits the project: an auto-fix pass runs each
command's own fix flag project-wide, then whatever is still failing is
handed to a dedicated fixer agent in a loop (reusing the same per-file
auto-fixing hook `run`'s generator uses) until clean or `max_rounds` is
reached — same clean-tree requirement as `run`, since it edits the selected
working directory in place with no worktree isolation. `--report-only`
never edits or fixes anything and never runs an agent; it exists for the
`lint-fix` skill, which runs it this way and does the fixing itself in the
calling Claude Code session instead.

## Claude Code plugin

This repo doubles as a Claude Code plugin: add it as a plugin source and these
skills become available in any project that also has a `.harness.toml`:

| Skill | Equivalent to |
|---|---|
| `/meow:sprint "<feature>"` | `meow run "<feature>"` |
| `/meow:meow-plan "<feature>"` | `meow plan "<feature>"` |
| `/meow:meow-review` | `meow review` |
| `/meow:meow-issue [ISSUE-KEY]` | `meow issue [ISSUE-KEY]` |
| `/meow:gitlab-review "<mr-url>"` | `meow gitlab-review "<mr-url>"` |
| `/meow:lint-fix` | `meow lint-fix --report-only`, then the skill fixes what it reports |

Each skill is a thin wrapper — see `skills/*/SKILL.md` — that shells out to the
same `meow` CLI, so it needs `meow` importable the same way (venv active,
or a `pipx install -e .`/global install).

## Onboarding a project

Copy a template from `templates/` to the target repo's root as
`.harness.toml`: `harness.toml.example` (annotated, language-neutral),
`harness.toml.python.example`, or `harness.toml.typescript.example`.

See [GUIDE.md](GUIDE.md) for the full field reference, the recommended `docs/`
layout, and a setup checklist — it's written for a Claude session onboarding a
different repo onto meow, generic to any language.

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
│   ├── sprint/SKILL.md                  # /meow:sprint  -> harness run
│   ├── meow-plan/SKILL.md               # /meow:meow-plan   -> harness plan
│   ├── meow-review/SKILL.md             # /meow:meow-review -> harness review
│   ├── meow-issue/SKILL.md              # /meow:meow-issue  -> harness issue
│   ├── gitlab-review/SKILL.md           # /meow:gitlab-review -> harness gitlab-review
│   └── lint-fix/SKILL.md                # /meow:lint-fix -> harness lint-fix --report-only
├── docs/
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
        ├── agents/
        │   ├── base.py                 # shared AgentContext/Agent base + ProjectContext
        │   ├── explorer.py             # explorer agent definition
        │   ├── planner.py              # planner agent
        │   ├── generator.py            # generator agent
        │   ├── reviewer.py             # reviewer agents and review helpers
        │   ├── issue_fetcher.py        # Jira MCP preflight + issue fetch, for `meow issue`
        │   ├── gitlab_fetcher.py       # GitLab MCP preflight + MR fetch, for `meow gitlab-review`
        │   └── lint_fixer.py           # lint-fix agent, for standalone `meow lint-fix`
        ├── orchestrator.py             # generator <-> reviewer round loop
        ├── issue_solver.py             # `meow issue` flow: fetch, worktree+branch, sprint, push
        ├── gitlab_reviewer.py          # `meow gitlab-review` flow: fetch MR, grade its diff
        ├── lint_fix.py                 # `meow lint-fix` flow: fix-or-report, standalone vs. skill
        └── cli.py                      # `meow` console-script entry point
```

# AGENTS.md

MEOW — Management, Execution & Optimization of Workflows: a Claude Agent SDK
harness (CLI `meow`) that is also a Claude Code plugin (`skills/`).

## Develop

From the repo root with `.venv` active (`python -m pip install -e ".[dev]"`):

```bash
python -m pytest -q        # the full suite; CI runs exactly this
ruff check .               # the lint gate (rules in pyproject.toml)
ruff format .              # formatting; CI runs `ruff format --check .`
```

Tests mix `unittest.TestCase` classes and plain pytest functions, so always run
them with pytest. Code run from the repo root imports the installed copy (the
`src/` layout is deliberate); from a linked worktree set `PYTHONPATH=src`.

Feature work can also run through the harness itself:
`meow run "<request>" --name "<feature-name>"` (plan, implement, review, and
deliver) or `meow plan ...` (plan only).

## Where things are documented

- [docs/CLI.md](docs/CLI.md): every command, flag, and flag combination.
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md): `.meow/config.toml` schema
  (lint, tester, build, permissions, Jira/GitLab/GitHub MCP, delivery).
- [docs/WORKFLOWS.md](docs/WORKFLOWS.md): workflow/agent formats, scopes, templates,
  catalog commands, and resume behavior.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): package ownership rules.
- [skills/_shared/native-mode.md](skills/_shared/native-mode.md): the native
  (in-session) protocol and the `meow native` JSON helpers.

Config layers, highest priority first: `.meow/config.local.toml`,
`.meow/config.toml`, `~/.meow/config.toml`. Plans, reviews, run journals, and
evidence live under `.meow/` (plans in `.meow/plans/` by default); project
docs stay in `docs/`.

## Plugin skills

`skills/*/SKILL.md`: `/meow:run` (CLI by default), `/meow:plan`,
`/meow:review`, `/meow:lint`, and `/meow:orchestrate` (native by default;
its task sprints still run as detached CLI workers); `/meow:onboard`
(add-ons; projects are onboarded automatically on first use),
`/meow:migration` (legacy layouts), and `/meow:customize`. Keep each skill's
CLI and native sections in step with docs/CLI.md.

## Code map

`src/meow/` is split by responsibility; put new code in the narrowest owning
package (see ARCHITECTURE.md) and keep the package root to entry points.

- `agents/`: SDK roles. Every role is `*Agent(context)` built on `Agent` in
  `agents/base.py`, which builds `ClaudeAgentOptions`, applies the permission
  policy, and runs one-shot queries. `SessionAgent` keeps one
  `ClaudeSDKClient` across rounds (generator, and the review/lint fixers in
  `fixers.py`). The Jira/GitLab/GitHub fetchers share `McpFetcherAgent` in
  `mcp_fetcher.py`; the orchestrator roles are in `orchestration.py`. `Sprint` and `ProjectContext` both satisfy `AgentContext`.
- `execution/`: round loops (`orchestrator.py`), `run`/`plan` flows
  (`sprint_runner.py`), workflow execution/session pools (`workflow/`),
  run journal (`run_state.py`), delivery, queue. `orchestration/` runs
  `meow orchestrate`: model, journal, scheduler, merges, triage, the shared
  `tick()`, final review, delivery, and the CLI driver `runner.py`.
- `cli/`: argument parsing, dispatch, `review_cli.py`, status, resume,
  workflow/agent catalog commands (`catalog_cli.py`), `orchestrate_cli.py`.
- `native/`: agent-free helpers behind `meow native ...` for the skills
  (`native_orchestrate.py` serves `/meow:orchestrate`).
- `project/`: config loading/schema, `[custom]` skill/agent discovery
  (`project/custom/`), onboarding, permissions, plan files,
  agent/workflow definitions, templates, and expressions (`project/custom/`).
  Editable role prose lives in `project/custom/builtin/agents/*.toml`;
  `project/prompts.py` builds context and code-owned verdict contracts.
  Change role prose in those definitions, never in a skill, so SDK and native
  modes stay identical.
- `infrastructure/`: lint, checks, test runner, worktrees, logging, usage,
  cancellation, background workers.
- `integrations/`: Jira, GitLab, GitHub, CI review, docs update, knowledge.
- `ui/`: the local `meow ui` catalog page and server; the repo registry
  it reads lives in `project/repos.py`.
- `hooks/`, `installer/`, `tasks/`, `evaluation/`: as named.

## Invariants worth knowing

- SDK `allowed_tools` auto-approves a tool without calling `can_use_tool`;
  gate tools with `agents.base.guard_tools`, not by adding a callback alone.
- Reviewer verdict files start `SUMMARY:` then `STATUS: PASS|FAIL`; parse them
  with `agents.reviewer._verdict_status` only.
- Plan, review, and tester file names come from `project/plan_files.py`
  (`plan_review_file`, `plan_test_file`, `new_review_filename`); never rebuild
  them inline.
- The orchestrator has one supervisor step, `execution/orchestration/tick.py`;
  the CLI runner and `meow native orchestrate` both call it. Nothing is pushed
  before the `delivering` phase, and the integration branch
  `orchestrator/<id>` never shares a ref prefix with a task branch
  (`orchestrator/<id>-<task>`).
- `run_sprint` marks a run `failed` whenever it raises before a terminal
  phase; inner steps only record phases, not failures.
- Agent definitions replace whole files by scope: local > project > user >
  builtin. Gate contracts and permission policy remain code-owned.
- Claude owns transcripts; the run journal stores only role-to-session IDs,
  resumed solely by `meow resume`.

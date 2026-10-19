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
`/meow:review`, `/meow:lint`, `/meow:schedule` (OS scheduling through CLI), and `/meow:orchestrate` (native by default;
its task sprints still run as detached CLI workers); `/meow:onboard`
(add-ons; projects are onboarded automatically on first use),
`/meow:migration` (legacy layouts), and `/meow:customize`. Keep each skill's
CLI and native sections in step with docs/CLI.md.

## Code ownership

Use [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for package and module ownership.
Put new code in the narrowest owning package; keep the package root to entry
points. Editable role prose belongs in
`src/meow/project/custom/builtin/agents/*.toml`, never in a skill.
`project/prompts.py` owns computed context and verdict contracts.

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

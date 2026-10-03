# MEOW

[![CI](https://github.com/maya8237/MEOW/actions/workflows/ci.yml/badge.svg)](https://github.com/maya8237/MEOW/actions/workflows/ci.yml)

MEOW (Management, Execution & Optimization of Workflows) is a configurable
planner, generator, and reviewer loop for software projects. It uses the
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview) and keeps
project-specific settings in the target repo's `.harness.toml`.

The planner creates a sprint plan and testable contract. The generator
implements it with lint feedback, and an independent reviewer checks the
result. Failed reviews feed back into the generator up to `max_rounds` times.

## Install

You do not need to create a virtual environment. You can install MEOW with
system Python or use an existing virtual environment. If you prefer a new
environment, create it in `.venv` in this repository; `.venv` is already in
`.gitignore`.

```bash
# Optional: create a local environment (ignored by Git)
python -m venv .venv

# Install into the selected Python environment
.venv/Scripts/python -m pip install -e .
```

Replace `.venv/Scripts/python` with `python -m pip` when using system Python
or an activated environment. On macOS or Linux, use `.venv/bin/python` for the
optional local environment. The install is editable, so engine changes take
effect immediately. When installed into a virtual environment, activate it or
use its full path to run `meow` from another project.

## Quick start

In a project configured with `.harness.toml`:

```bash
meow run "Add CSV export" --name "add-csv-export"
```

By default, feature work uses an isolated worktree. To set up a project,
copy a template from `templates/` to its root as `.harness.toml`, then use the
[/meow:onboard guide](skills/onboard/SKILL.md).

## Commands

- `meow run "<feature>" --name "<name>"` plans, implements, and reviews work.
- `meow plan "<feature>" --name "<name>"` writes a plan without implementing it.
- `meow review` reviews the latest plan, or the code diff if no plan exists.
- `meow run --jira [ISSUE-KEY]` solves a Jira issue in a worktree and pushes
  its verified branch; configure the optional Jira integration first.
- `meow run --lint-fix` runs configured project-wide linters and fixes findings.

All commands accept `--working-dir PATH`. The [CLI guide](docs/CLI.md) covers
review sources, worktree and resume options, native mode, and unattended runs.

## Claude Code skills

This repository is also a Claude Code plugin. It provides `/meow:run`,
`/meow:plan`, `/meow:review`, `/meow:lint`, and `/meow:onboard` skills.
They run natively in the calling session by default; ask for CLI mode to use
the `meow` command instead. Both modes share configuration and output formats.

### Configured tests and exploratory review

Tester mode is opt-in with `meow run --test` or
`meow review --plan-file PATH --test`. After a passing plan review, MEOW runs
configured test commands and then an exploratory tester agent. Failed blocking
commands or tester findings feed the next generation round; `max_rounds`
counts the combined review/test rounds. In a monorepo, each `[[lint]]` and
`[[tester.tests]]` entry may set its own `cwd`; lint entries also support
component `include` and `exclude` path prefixes. See
[`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md#monorepo-lint-and-tester).

MEOW reads architecture context from `docs/ARCHITECTURE.md` or
`ARCHITECTURE.md`.

## Documentation

- [CLI guide](docs/CLI.md): command options and execution modes.
- [Integration guide](docs/INTEGRATIONS.md): Jira, GitLab, scheduled runs,
  and error reference.
- [Architecture](ARCHITECTURE.md): workflow, module responsibilities, and agent
  contracts.
- [Project instructions](AGENTS.md): repository-specific development rules.


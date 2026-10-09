# MEOW

[![CI](https://github.com/maya8237/MEOW/actions/workflows/ci.yml/badge.svg)](https://github.com/maya8237/MEOW/actions/workflows/ci.yml)

MEOW (Management, Execution & Optimization of Workflows) is a configurable
planner, generator, and reviewer loop for software projects. It uses the
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview) and keeps
shareable project settings in `.meow/config.toml`. Settings are resolved from
highest to lowest priority as `.meow/config.local.toml`, project
`.meow/config.toml`, the user fallback `~/.meow/config.toml`, and built-in
defaults. The local project file is ignored; the shared project file is
trackable.

> **Start here:** Run `/meow:onboard` in the repository where you want to use
> MEOW. It guides setup and enables the features you need. Harness
> configuration is optional.

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

Install MEOW, then run `/meow:onboard` in your target repository. After setup,
start feature work with:

```bash
meow run "Add CSV export" --name "add-csv-export"
```

## Commands

- `meow run "<feature>" --name "<name>"` plans, implements, and reviews work.
- `meow plan "<feature>" --name "<name>"` writes a plan without implementing it.
- `meow review` reviews a plan or code diff.
- `meow run --jira [ISSUE-KEY]` solves a Jira issue when Jira is configured.
- `meow run --lint-fix` runs configured linters and fixes findings.
- `meow ipython` opens the interactive session explicitly; bare `meow` does the
  same when no arguments are supplied.

All commands accept `--working-dir PATH`. See the [CLI guide](docs/CLI.md) for
advanced options.

## Claude Code skills

This repository is also a Claude Code plugin. It provides `/meow:run`,
`/meow:plan`, `/meow:review`, `/meow:lint`, `/meow:onboard`, and
`/meow:migration` skills.

## Documentation

- [CLI guide](docs/CLI.md): command options and execution modes.
- [Integration guide](docs/INTEGRATIONS.md): Jira, GitLab, scheduled runs,
  and error reference.
- [Architecture](ARCHITECTURE.md): workflow, module responsibilities, and agent
  contracts.
- [Project instructions](AGENTS.md): repository-specific development rules.


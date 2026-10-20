# MEOW

<p align="center">
  <img src="logo.png" width="160" alt="MEOW pixel cat logo" />
</p>

> Tell MEOW what you want. Get the finished branch.

MEOW is a workflow harness for Claude that plans, implements, tests, reviews,
and delivers software changes in isolated worktrees.

Running a built-in workflow starts a loop: MEOW implements, reviews, and fixes
the work until review passes, or the configured round limit is reached. It does
not stop after a single attempt. Once review passes, MEOW runs the final checks
before delivery. Custom workflows control their own loop stages.

## Install

### PowerShell

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
```

### Bash

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
```

The installer needs Python 3.12 or newer (it uses the newest it finds; set
`MEOW_PYTHON` to choose one). It puts MEOW in `~/.meow`, adds the `meow` command to your PATH,
and sets up its skills in Claude Code. Open a new terminal or restart Claude Code
to get started.

## Run

Claude Code:

```text
/meow:run Add CSV export
```

Terminal or CI:

```bash
meow run "Add CSV export" --name csv-export
```

By default, `meow run` handles the request as one sprint: plan, implement,
check, and review.

Add `-o/--orchestrate` to split an objective into tasks, run
each as a full sprint in its own worktree, and merge passing tasks. MEOW
reviews the combined result before pushing one branch and opening one PR/MR.

Projects are set up automatically on first use. Use `/meow:onboard` to configure
integrations and optional features.

## Common commands

| Need | Command |
| --- | --- |
| Build a feature | `/meow:run ...` or `meow run ...` |
| Plan only | `/meow:plan ...` or `meow plan ...` |
| Schedule a run | `/meow:schedule ...` or `meow schedule add --at "09:00" -- run "Add CSV export" --name csv-export` |
| Review | `/meow:review ...` or `meow review ...` |
| Integrate CI review | `/meow:integrate-ci-review` |
| Fix lint | `/meow:lint` |
| Queue work | `meow queue "..."` |
| Check or continue | `meow status` or `meow resume RUN_ID` |

### Common flags

| Flag | Purpose |
| --- | --- |
| `--orchestrate` / `-o` | Coordinate multiple sprints and deliver one reviewed branch and PR/MR |
| `--max-parallel N` | Limit concurrent sprints with `--orchestrate` (default: 3) |
| `--auto` / `-a` | Run a feature automatically |
| `--name NAME` | Name the feature branch and worktree |
| `--from BRANCH` / `-b BRANCH` | Choose the starting branch |
| `--update` / `-u` | Update MEOW with `meow --update` |
| `--help` / `-h` | Show help for a command |

See the [CLI guide](docs/CLI.md) for worktrees, background runs, recovery,
orchestration, and advanced options.

## Configuration and integrations

- `.meow/config.toml` — shareable project settings.
- `.meow/config.local.toml` — ignored machine- or user-specific settings.
- `~/.meow/config.toml` — user fallback settings.

MEOW supports Jira, GitLab, GitHub, configured tests and builds, browser
tester checks, Claude Code hooks, and scheduled Jira runs. See the
[integration guide](docs/INTEGRATIONS.md).

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [CLI guide](docs/CLI.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Project instructions](AGENTS.md)
- [Contributing](CONTRIBUTING.md)
- [Roadmap](docs/ROADMAP.md)
- [Changelog](CHANGELOG.md)
- [License](LICENSE)

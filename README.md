# MEOW

<p align="center">
  <img src="logo.png" width="160" alt="MEOW pixel cat logo" />
</p>

> Tell MEOW what you want. Get the finished branch.

MEOW is a workflow harness for Claude that plans, implements, tests, reviews,
and delivers software changes in isolated worktrees.

## Install

### PowerShell

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
```

### Bash

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
```

Run `meow --update` to update an existing installation. Public commands and
`/meow:*` skills warn when a newer version is available.

## Run

Claude Code:

```text
/meow:run Add CSV export
```

Terminal or CI:

```bash
meow run "Add CSV export" --name csv-export
```

Projects are set up automatically on first use. Use `/meow:onboard` to configure
integrations and optional features.

To run later, ask `/meow:schedule` to run your request at a future time, use
`meow schedule add --at "09:00" -- run "Add CSV export" --name csv-export`,
or choose **Schedule for later** in `meow ui`. Scheduling uses Windows Task
Scheduler or Linux systemd user timers; see the [CLI guide](docs/CLI.md#scheduling-a-run)
for timezones, platform requirements, and cancellation.

## Common commands

| Need | Command |
| --- | --- |
| Plan only | `/meow:plan ...` or `meow plan ...` |
| Several related changes at once | `/meow:orchestrate ...` or `meow orchestrate "..."` |
| Review | `/meow:review ...` or `meow review ...` |
| Integrate CI review | `/meow:integrate-ci-review` |
| Fix lint | `/meow:lint` |
| Queue work | `meow queue "..."` |
| Check or continue | `meow status` or `meow resume RUN_ID` |
| Browse definitions or manage command sessions | `meow ui` |

`meow orchestrate "Add SSO login and an admin audit log"` splits an objective
into tasks, runs them in parallel, merges them into one branch, reviews it as
a whole, and opens a single pull request.

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

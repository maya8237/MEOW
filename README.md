# MEOW

<p align="center">
  <img src="logo.png" width="160" alt="MEOW pixel cat logo" />
</p>

> Describe a feature. Get a finished MR.

MEOW plans, builds, tests, and reviews complete features, then opens a PR.
Use it from Claude Code with a few simple skills.

[Start here](https://maya8237.github.io/MEOW/) · [Skills](docs/SKILLS.md) · [Customize](skills/customize/SKILL.md)

## Install

### PowerShell

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
```

### Bash

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
```

You’ll need **Python 3.12+** and **Claude Code with authentication set up**.
Restart Claude Code after installation. [Installation details](docs/INSTALL.md).

## Set up your project

Open your project in Claude Code and run:

```text
/meow:onboard
```

MEOW walks you through setup, integrations, and optional features.
Basic project setup also happens automatically on first use.

## Try a skill

Tell MEOW what you want:

```text
/meow:run Add a dark mode toggle to the settings page
```

Feature runs use a separate worktree. After checks pass, MEOW pushes the branch,
opens a PR/MR into your configured target, and verifies its CI pipeline.

- `/meow:plan` — think through a change before building.
- `/meow:review` — review existing code and fix findings.
- `/meow:lint` — fix your project’s lint findings.
- `/meow:schedule` — run a task later.

[See all skills and examples](docs/SKILLS.md).

## Make it yours

```text
/meow:customize
```

Ask for a skill for your coding conventions, a reviewer with a specific focus,
or a workflow with your own steps. [Customization guide](skills/customize/SKILL.md).

## Reference

- [CLI commands and flags](docs/CLI.md)
- [Runs and recovery](docs/RUNS.md)
- [Configuration and integrations](docs/INTEGRATIONS.md)
- [Agents and workflows](docs/WORKFLOWS.md)
- [Multi-task orchestration](docs/ORCHESTRATION.md)
- [MR/PR and pipeline verification](docs/PIPELINE.md)
- [Worktree setup](docs/WORKTREE_SETUP.md) · [Cleanup](docs/CLEANUP.md) · [Local UI](docs/UI.md)
- [Update and uninstall](docs/INSTALL.md#update)

## Contribute

- [Architecture](docs/ARCHITECTURE.md)
- [Project instructions](AGENTS.md)
- [Contributing](CONTRIBUTING.md)
- [Releasing](docs/RELEASING.md)
- [Roadmap](docs/ROADMAP.md)
- [License](LICENSE)

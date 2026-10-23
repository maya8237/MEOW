# MEOW

<p align="center">
  <img src="logo.png" width="160" alt="MEOW pixel cat logo" />
</p>

> Tell MEOW what you want. Get the finished branch.

MEOW helps Claude plan, build, test, and review your code.
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
/meow:run Add CSV export
```

Feature runs use a separate worktree, then commit and push after checks pass.

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
- [Update and uninstall](docs/INSTALL.md#update)

## Contribute

- [Architecture](docs/ARCHITECTURE.md)
- [Project instructions](AGENTS.md)
- [Contributing](CONTRIBUTING.md)
- [Roadmap](docs/ROADMAP.md)
- [License](LICENSE)

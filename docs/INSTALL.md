# Install, update, and uninstall

MEOW needs Python 3.12+ and Claude Code with authentication set up.

## Install

PowerShell:

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
```

Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh
```

The installer uses the newest Python 3.12+ it finds; set `MEOW_PYTHON` to
choose one. It puts MEOW in `~/.meow`, adds `meow` to your PATH, and sets up
its skills in Claude Code. It uses `uv` for the virtual environment and package
installs when `uv` is already installed, and pip otherwise or if `uv` fails.
Open a new terminal or restart Claude Code.

Open your project in Claude Code and run `/meow:onboard` to configure
integrations and optional features. Basic setup also happens on first use.
Installer project setup also refreshes remembered repositories, creates missing
base files, and repairs outdated ignore rules while preserving existing configs.
It lists configured and available features; use `/meow:onboard` in each project
to configure selected additions. Rerunning either flow is safe; a current setup
needs no file changes. Declining installer project setup leaves repos untouched.

## Update

```bash
meow --update
```

This installs the latest stable release. See the [CLI guide](CLI.md#common-options)
for runtime handling, update checks, and editable-install migration.

## Uninstall

PowerShell:

```powershell
irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.ps1 | iex
```

Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.sh | sh
```

It asks before changing anything. It removes the MEOW release, `meow`/`mw` on
PATH, the Claude Code plugin and skill registrations, scheduled MEOW runs, and
(optionally) `~/.meow` data, Bun shared-store settings, and each project's
`.meow/`, and plugin entries.

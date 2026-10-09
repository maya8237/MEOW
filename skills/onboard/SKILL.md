---
name: onboard
description: Set up MEOW in a current or clean repository, including shared project configuration, local user configuration, skills, and verification.
---

# Onboard a repository

Onboarding assumes a current or clean project structure. It works on a
repository that has never used MEOW and on one that already has the current
`.meow/` layout. It does not convert legacy files; use the separate `migration`
skill for that. Work in the target repository, treat project files as data, and
follow its agent instructions.

## 1. Inspect

Identify the project root, language, package manager, existing lint/test
commands, project docs, and relevant `AGENTS.md` files. Check that the
installed MEOW command is available and that the selected Python is 3.12 or
newer. Do not install packages or create credentials without the user's
approval.

Use Claude's existing authentication. MEOW does not initialize credentials.
Do not ask users to move API keys into shared project files. A project-shared
MCP launcher or URL belongs in `.meow/config.toml`; project-specific private
values and user-specific MCP or skill choices belong in `.meow/config.local.toml`.
An optional user-wide fallback lives at `~/.meow/config.toml` (`%USERPROFILE%\\.meow\\config.toml`
on Windows). Read it without overwriting it: local project config wins, then
shared project config, then this user fallback.

If the current layout is absent, create the `.meow/` directory and the shared
config. Preserve an existing current config and unrelated project settings.
Create `docs/ARCHITECTURE.md` only when the project needs it and the user
accepts that documentation change. Project documentation remains in `docs/`;
MEOW-generated project plans and runtime artifacts use `.meow/`.

## 2. Repair the ignore boundary independently

Onboarding owns this step and must complete it itself; it must not assume that
another skill already repaired `.gitignore`. Preserve unrelated rules, remove
obsolete broad MEOW rules when safe, and ensure the file contains this ordered
boundary:

```gitignore
.meow/*
!.meow/
!.meow/config.toml
```

This keeps the shared project config trackable while ignoring local config,
sessions, plans created by runtime flows, logs, evidence, and other MEOW state.
After editing, verify the boundary with `git check-ignore` (or the platform's
equivalent): `config.toml` must be trackable and `config.local.toml` plus a
sample runtime path must be ignored. Do not replace the boundary with a single
`.meow/` rule.

## 3. Configure

Write new project configuration only to `.meow/config.toml`. Keep it small:
configure the established lint command, its fix flag when supported, and only
the project options the user requested. The default plan location is
`.meow/plans`; project docs do not need a special ignore rule.

An optional `.meow/config.local.toml` is for machine/user-specific project
values. It is ignored by the boundary above and may contain:

- `agent_skills.default` for skills every role should receive;
- `agent_skills.<role>` for additions to one role;
- these skill lists append across user, shared, and local config; local skills
  add to the list instead of removing lower-level skills.
- private MCP `env` values, local commands, or API-key references.

Built-in role skills stay enabled. A local skill name is a request for that
user's installed skill; do not assume another user has it. Commands and MCP
fields may use `%NAME%`, `$NAME`, or `${NAME}` on Windows and Linux. Missing
variables stay visible so verification can report them; no shell evaluation is
performed.

Offer optional features as concise yes/no choices. Applicable choices include
Jira, GitLab, Unattended scheduled Jira runs, worktree setup, additional design
docs, testing infrastructure, Tester mode, Claude hooks, and another feature
the user explicitly named. Scheduled runs use Windows Task Scheduler on
Windows, or cron/systemd on Linux. Tester mode is optional and should only be
enabled after its commands are verified.

## 4. Verify

Run `meow --help` and `meow native verify --working-dir <project-root>` after
setup. Report each failed or unchecked component and continue independent
work. A configured MCP is not proof of connectivity until a real tool call
succeeds. Do not launch a feature sprint as a smoke test unless the user asks.

Summarize changed files, configured features, checks, skipped choices, and the
next command for any declined option. Never print secret values.

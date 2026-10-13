# Changelog

All notable changes to MEOW will be documented here.

## Unreleased

- Pending schedules can be rescheduled with `meow schedule edit ID --at TIME`
  or **Edit time** in UI session details, preserving their saved run settings.

- One-shot scheduling through `meow schedule`, `/meow:schedule`, and the UI's
  **Schedule for later** option, using Windows Task Scheduler or Linux systemd
  user timers. Schedules preserve reviewed arguments, support cancellation,
  and record execution results without keeping the current session open.

- `meow orchestrate` and `/meow:orchestrate`: split an objective, Jira issues,
  a JQL query, or a task file into a graph of tasks, run each as its own
  unattended sprint in parallel, recover failures through triage, merge every
  passing task into one `orchestrator/<id>` branch, review it against the
  objective, push it, and open a pull or merge request. A new `[orchestrator]`
  config table sets the limits; `meow worktree clean <id>` removes a delivered
  orchestrator's worktrees.

- Windows: every child process (git, linters, tests) and tester dev servers
  start with `CREATE_NO_WINDOW`, so no stray `git.exe` or `bun.exe` console
  windows appear.
- Planner task graphs accept ids such as `task-1` or `1` (letters, digits, `-`,
  `_`) instead of failing with "task IDs must be nonempty identifiers".
- Tester dev servers: when the `ready_url` port is already in use, MEOW starts
  the server on a free port instead of failing. `{port}` in `command`, `args`,
  or `env` is replaced and `PORT` is exported; the real URL goes to the tester
  and to browser providers as `MEOW_DEV_SERVER_URL`.
- The installer now registers MEOW's checkout as a user-wide Claude Code
  plugin through its marketplace (`extraKnownMarketplaces` and
  `enabledPlugins` in `~/.claude/settings.json`) instead of the
  `CLAUDE_CODE_PLUGIN_DIRS` variable, so `/meow:*` skills (including
  `/meow:migration`) load after restarting Claude Code; a legacy entry is removed.
- Onboarding, which runs on first use and from the installer, now migrates a
  legacy harness config file to `.meow/config.toml` automatically (`docs_dir`
  dropped, legacy file kept). `/meow:migration` covers the rest.
- Claude Code hooks now return `additionalContext` / `systemMessage`, so lint
  failures and advisories actually reach the session.
- A lint program that cannot start is reported as a failing command instead
  of aborting the review, lint-fix, or post-edit hook.
- A failing mandatory tester command now rewrites the saved tester report to
  `STATUS: FAIL`; report-only review runs are terminal, and `meow status`
  only suggests `meow resume` for resumable runs.
- `meow docs-update` follows `[delivery].target_branch`; the Windows installer
  shows its update prompts when input is piped.
- Custom skills and agents: `[custom]` lists directories of Claude Code-format
  skills and agent files in local project, project (versioned, never
  git-ignored), or user scope. CLI roles load them through a generated
  `meow-custom` plugin and as capped subagents; native skills read them with
  `meow native custom`. `/meow:customize` now scaffolds and registers them.
- Projects are onboarded automatically on the first command that needs a config
  (`run`, `plan`, `review`, lint, queue, Jira, native `prepare`): the ignore
  boundary plus a minimal `.meow/config.toml` are created in the active
  checkout. Linked-worktree delivery includes those changes; in-place runs
  leave them for the user. `/meow:onboard` is now for add-ons and reports gaps
  via `meow native onboard-status`.
- `meow resume --continue` no longer fails once a run's plan is in progress,
  and any `meow run` failure now leaves the run marked `failed`.
- `meow review --ci` accepts merge requests that target the configured
  `[delivery].target_branch` instead of only `dev`.
- A blocking lint failure now always rewrites the saved verdict to
  `STATUS: FAIL`; branch reviews see newly created files; requests that
  mention "either/whether ... or" are no longer stopped as product decisions.
- Clarified the one-command workflow and first-run path in the README.
- Framed MEOW as simple harness engineering around Claude Agent SDK sessions.
- Added GitHub pull-request review, GitLab CI review, optional Claude Code
  hooks, browser-aware tester checks, and durable queue/recovery workflows.

## 0.1.0 - pending public release

- Claude Agent SDK is the execution backend for the MEOW workflow.
- Claude Code skills and the `meow` CLI share the same plan, implement,
  verify, review, checkpoint, and delivery concepts.
- Project templates support repeatable configuration and integrations.

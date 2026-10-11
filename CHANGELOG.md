# Changelog

All notable changes to MEOW will be documented here.

## Unreleased

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

# Changelog

All notable changes to MEOW will be documented here.

## Unreleased

- Projects are onboarded automatically on the first command that needs a config
  (`run`, `plan`, `review`, lint, queue, Jira, native `prepare`): the ignore
  boundary plus a minimal `.meow/config.toml` are created in the active
  checkout. Linked-worktree delivery includes those changes; in-place runs
  leave them for the user. `/meow:onboard` is now for add-ons and reports gaps
  via `meow native onboard-status`.
- Clarified the one-command workflow and first-run path in the README.
- Framed MEOW as simple harness engineering around Claude Agent SDK sessions.
- Added GitHub pull-request review, GitLab CI review, optional Claude Code
  hooks, browser-aware tester checks, and durable queue/recovery workflows.

## 0.1.0 - pending public release

- Claude Agent SDK is the execution backend for the MEOW workflow.
- Claude Code skills and the `meow` CLI share the same plan, implement,
  verify, review, checkpoint, and delivery concepts.
- Project templates support repeatable configuration and integrations.

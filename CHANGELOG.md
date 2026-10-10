# Changelog

All notable changes to MEOW will be documented here.

## Unreleased

- Projects are onboarded automatically on the first command that needs a config
  (`run`, `plan`, `review`, lint, queue, Jira, native `prepare`): ignore boundary
  plus a minimal `.meow/config.toml`, delivered with the run. `/meow:onboard` is
  now for add-ons and reports gaps via `meow native onboard-status`.
- Clarified the one-command workflow and first-run path in the README.
- Framed MEOW as simple harness engineering around Claude Agent SDK sessions.
- Added a reproducible terminal demo source and workflow visualization.
- Added contribution, issue-reporting, roadmap, and launch-pack scaffolding.

## 0.1.0 - pending public release

- Claude Agent SDK is the execution backend for the MEOW workflow.
- Claude Code skills and the `meow` CLI share the same plan, implement,
  verify, review, checkpoint, and delivery concepts.
- Project templates support repeatable configuration and integrations.

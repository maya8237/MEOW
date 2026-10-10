# Roadmap

MEOW's near-term direction is to make harness engineering feel simple: the
handoff from request to verified branch should be predictable without making
users learn a large workflow system.

## Shipped

- Claude Agent SDK execution with Claude Code skill entry points.
- Reusable project configuration and templates.
- Isolated worktrees, checkpoints, bounded quality gates, and review loops.
- CLI and skill paths for feature requests, queues, Jira, GitLab, and GitHub review.
- Optional Claude Code host hooks, browser-aware tester checks, and durable
  status/cancel/resume recovery.

## Next

- More first-run examples that can be copied into real repositories.
- A versioned run-event API for CI status and notifications (distinct from the
  shipped Claude Code host hooks).
- Additional integrations when they preserve the same simple handoff.

See [Future features](FUTURE_FEATURES.md) for deferred ideas and their safety
constraints. Open an issue if your workflow would benefit from a different
integration or delivery boundary.

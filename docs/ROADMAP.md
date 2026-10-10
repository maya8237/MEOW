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
- Custom skills and agents discovered from `[custom]` directories in local
  project, project, and user scope, with precedence, validation, and a
  tool cap so custom agents never exceed their parent role.

## Next

- More first-run examples that can be copied into real repositories.
- A versioned run-event API for CI status and notifications (distinct from the
  shipped Claude Code host hooks).
- Additional integrations when they preserve the same simple handoff.

## Future features

### Versioned run events and notifications

**Status:** Deferred. Do not implement as part of the current feature work.

MEOW could publish a small, versioned set of events at meaningful run
boundaries: run started, plan accepted, phase started or completed,
verification failed, run paused, and delivery completed. Each event would
identify the run, phase, time, and path to supporting evidence. Checkpoints and
`meow status` should reflect the same transitions.

This is separate from the shipped Claude Code host hooks, which are optional
editor integrations installed and removed with `meow hooks`.

Projects could configure optional handlers for uses such as CI status or
notifications. Reporting handlers should not change a verified run's result
when they fail. A handler explicitly configured as a required gate may block
completion, and its failure should be recorded. Handlers need timeouts and
declared permissions. In `--unattended` mode, they must never prompt for
input; failures must leave a clear checkpoint and recovery path.

Add event types only when a concrete consumer needs them. Avoid exposing every
internal agent message or tool action as a public event.

Open an issue if your workflow would benefit from a different integration or
delivery boundary.

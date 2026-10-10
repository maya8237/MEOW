# MEOW package architecture

`src/meow` is organized by responsibility. New code belongs in the narrowest
package that owns the workflow, and callers should import from that package
rather than creating top-level compatibility modules.

- `agents/`: Claude Agent SDK role implementations. This includes exploration,
  planning, generation, review, testing, fixing, documentation, lint, and
  issue/MR/PR fetching roles.
- `cli/`: the public command surface and dispatch for `run`, `plan`, `review`,
  status/recovery, queues, hooks, IPython, and hidden maintenance commands.
- `execution/`: sprint orchestration, delivery, plan approval, queue/run
  policy, and durable run-state coordination.
- `evaluation/`: read-only evaluation reports over durable run journals.
- `frontend/`: frontend and browser-capability discovery helpers.
- `infrastructure/`: shared operational services such as lint/test execution,
  checks, logging, cancellation, background workers, usage accounting, and
  worktree lifecycle/setup.
- `integrations/`: Jira issue solving, GitLab/GitHub review, branch/CI review,
  documentation updates, and knowledge-document adapters.
- `native/`: deterministic helpers used by in-session skills, including
  preparation, prompts, lint, checkpoints, state, and the native CLI.
- `project/`: configuration models/schema, onboarding, permissions, planning
  files/state, pre-plan shaping, prompts, and command policy.
- `hooks/`: optional Claude Code host-hook handlers and installation support.
- `installer/`: installation and plugin/package setup helpers.
- `tasks/`: task models, execution, scheduling, and integration runners.

The active review command is in `cli/review_cli.py`, with review roles in
`agents/` and provider-specific flows in `integrations/`. `__main__.py`
exposes the installed console entry point. Tests remain under the repository's
`tests/` directory and are not copied into the runtime package.

The `src/` layout is deliberate: running from the repository root reaches the
installed copy, so a broken editable install is caught rather than masked.

New code belongs in the narrowest responsibility package. A package is split
again when its files serve different workflows or require different
dependencies; file count alone is not a threshold. The runtime has no
top-level alias modules; use the responsibility packages listed above.
Empty placeholder packages are not kept: a directory exists only when it
contains implementation modules or a documented package boundary.
Within a responsibility package, prefer creating or reusing subpackages for
related modules instead of accumulating many floating files at the package
root. Two to four directly owned files can be reasonable when the boundary is
clear, but this is a guideline rather than a hard limit; keep the package
focused and use subpackages whenever they improve discoverability.

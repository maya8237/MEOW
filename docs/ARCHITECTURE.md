# MEOW package architecture

`src/meow` is organized by responsibility. Each package owns its canonical
implementation modules; callers should import from the package that owns the
workflow.

- `agents/`: SDK role implementations. Role families should be split into
  subpackages when they gain unrelated responsibilities.
- `native/`: deterministic, in-session skill execution and its CLI, lint,
  preparation, prompt, and checkpoint components.
- `execution/`: sprint orchestration, runners, and durable run state.
- `project/`: configuration, plans, shaping, prompts, and permissions.
- `infrastructure/`: checks, lint, tests, logging, cancellation, and
  worktree lifecycle.
- `integrations/`: Jira, GitLab, GitHub, issue-solving, and knowledge
  adapters.
- `tasks/`: task models, execution, scheduling, and integration.
- `cli/`: the installed command entry point.

New code belongs in the narrowest responsibility package. A package is split
again when its files serve different workflows or require different
dependencies; file count alone is not a threshold. The runtime has no
top-level alias modules; use the responsibility packages listed above.
Empty placeholder packages are not kept: a directory exists only when it
contains implementation modules or a documented package boundary.
Tests stay in the repository `tests/` package and are not copied into the
runtime package.

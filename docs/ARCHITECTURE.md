# MEOW package architecture

`src/meow` is organized by responsibility. Each package owns its canonical
implementation modules; callers should import from the package that owns the
workflow.

- `agents/`: SDK role implementations. Role families should be split into
  subpackages when they gain unrelated responsibilities.
- `native/`: deterministic, in-session skill execution and its CLI, lint,
  preparation, prompt, and checkpoint components.
- `execution/`: sprint orchestration, runners, and durable run state.
- `review/`: review command and branch review flows.
- `integrations/`: Jira and GitLab adapters.
- `testing/`: test execution and verification gates.
- `worktrees/`: Git worktree lifecycle.
- `cli/`: the installed command entry point.

New code belongs in the narrowest responsibility package. A package is split
again when its files serve different workflows or require different
dependencies; file count alone is not a threshold. The runtime has no
top-level alias modules; use the responsibility packages listed above.
Tests stay in the repository `tests/` package and are not copied into the
runtime package.

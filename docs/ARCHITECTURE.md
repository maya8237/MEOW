# MEOW package architecture

`src/meow` is organized by responsibility. Put new code in the narrowest
package that owns the workflow and import it from there; there are no
top-level alias or compatibility modules.

- `agents/`: Claude Agent SDK roles: exploration, planning, generation,
  review, testing, fixing, documentation, and Jira/GitLab/GitHub fetching.
- `cli/`: the public command surface and dispatch for `run`, `plan`, `review`
  (`review_cli.py`), status/recovery, queues, hooks, IPython, and hidden
  maintenance commands.
- `execution/`: sprint orchestration, delivery, plan approval, queue/run
  policy, and the durable run journal.
- `evaluation/`: read-only evaluation reports over run journals.
- `infrastructure/`: lint/test execution, checks, logging, cancellation,
  background workers, usage accounting, and worktree lifecycle/setup.
- `integrations/`: Jira issue solving, GitLab/GitHub/CI review, documentation
  updates, and knowledge documents.
- `native/`: deterministic helpers behind `meow native ...` for in-session
  skills.
- `project/`: config models/schema, custom skill and agent discovery
  (`project/custom/`), onboarding, permissions, plan files/state, pre-plan
  shaping, prompts, and command policy.
- `hooks/`: optional Claude Code hook handlers and installation.
- `installer/`: post-install plugin registration and project onboarding.
- `tasks/`: task-graph models, scheduling, execution, and integration.

`__main__.py` is the console entry point. Tests live in `tests/` and are not
packaged. The `src/` layout is deliberate: running from the repository root
reaches the installed copy, so a broken editable install is caught rather than
masked.

Split a package when its files serve different workflows or need different
dependencies, not because of file count. Prefer a subpackage for a cluster of
related modules over many loose files at a package root, and keep no empty
placeholder packages.

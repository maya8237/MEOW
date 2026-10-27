# MEOW package architecture

`src/meow` is organized by responsibility. Put new code in the narrowest
package that owns the workflow and import it from there; there are no
top-level alias or compatibility modules.

- `agents/`: Claude Agent SDK roles: exploration, planning, generation,
  review, testing, fixing, documentation, and Jira/GitLab/GitHub fetching.
  `agents/orchestration.py` holds the orchestrator's decomposer, triage,
  integrator, and pull-request roles. Roles take an `AgentContext` (satisfied by
  `Sprint` and `ProjectContext`); `agents/base.py` builds options and permissions.
  `SessionAgent` retains one client for generator/fixer rounds;
  `McpFetcherAgent` in `mcp_fetcher.py` shares fetching behavior across providers.
- `cli/`: the public command surface and dispatch for `run`, `plan`, `review`
  (`review_cli.py`), status/recovery, queues, IPython, and hidden
  maintenance commands. `catalog_cli.py` owns workflow/agent inspection and ejection;
  `orchestration_cli.py` owns multi-sprint execution behind `meow run`.
- `execution/`: sprint orchestration, delivery, plan approval, queue/run
  policy, and the durable run journal. `orchestrator.py` owns round loops,
  `sprint_runner.py` run/plan flows, and `run_state.py` the journal.
  `execution/workflow/` owns the expanded
  step engine, built-in operation adapters, and loop session pools.
  `final_delivery.py` opens the run's MR and closes the deliver step;
  `pipeline_verify.py` owns the bounded wait/fix loop and outcomes,
  `pipeline_classify.py` job classification (see [pipeline](PIPELINE.md)).
  `execution/orchestration/` owns the orchestrator: its journal and driver
  lock, task scheduling and child processes, merges into the integration
  branch, triage, the shared supervisor `tick()`, the whole-objective review,
  delivery, and the CLI driver (`runner.py`).
- `common/`: dependency-free primitives. `safe_platform.py` keeps `platform`
  queries off WMI (installed first by `meow/__init__.py`); use `sys.platform`,
  never `platform.system()`. `process.py` owns process identity and
  `run_windows_tool` (timeout-bounded `taskkill`).
- `evaluation/`: read-only evaluation reports over run journals.
- `infrastructure/`: lint/test execution, checks, logging, cancellation,
  background workers, usage accounting, and worktree lifecycle/setup.
  `scheduling.py` owns durable one-shot schedules and execution claims;
  `scheduled_tasks.py` registers Windows Task Scheduler and Linux systemd triggers.
- `integrations/`: Jira issue solving, GitLab/GitHub/CI review, documentation
  updates. `pipeline_*.py` are the GitLab/GitHub
  pipeline providers behind `pipeline_verify.py`.
- `native/`: deterministic helpers behind `meow native ...` for in-session
  skills, including `native_orchestrate.py` for `/meow:run --orchestrate`.
- `project/`: config models/schema, custom skill and agent discovery
  (`project/custom/`), onboarding and its update announcements, permissions,
  plan files/state, prompts, and command policy. `project/custom/` owns scoped agent
  and workflow loading, expression/template validation, and shipped TOML
  definitions under `builtin/`. `project/prompts.py` supplies computed context
  and verdict contracts; editable role prose belongs in the agent definitions.
- `installer/`: shared checkout updates and editable installation in the
  stdlib-only `maintenance.py`, plus post-install plugin registration and
  project onboarding. Both streamed scripts and `meow --update` use it.
- `tasks/`: task-graph models, scheduling, execution, and integration.
- `ui/`: local catalog/session server and bundled frontend; its repository
  registry lives in `project/repos.py`.

`__main__.py` is the console entry point. Tests live in `tests/` and are not
packaged. The `src/` layout is deliberate: running from the repository root
reaches the installed copy, so a broken editable install is caught rather than
masked.

Split a package when its files serve different workflows or need different
dependencies, not because of file count. Prefer a subpackage for a cluster of
related modules over many loose files at a package root, and keep no empty
placeholder packages.

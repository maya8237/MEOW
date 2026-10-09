# Architecture

MEOW is a configurable planner, generator, and reviewer loop. The engine lives
under `src/meow/`; project-specific settings are loaded from the target
project's `.meow/config.toml`, with a higher-priority local overlay, a
user-level `~/.meow/config.toml` fallback, and a legacy `.harness.toml`
compatibility fallback at the project layer.

## Workflow

The planner writes a sprint plan and testable Sprint Contract. The generator
implements the plan with lint feedback. The reviewer independently checks the
result and returns PASS or FAIL. A failed review feeds back to the generator,
up to the configured `max_rounds` limit.

`orchestrator.py` owns the shared generator/reviewer round loops.
`sprint_runner.py` implements `meow run` and `meow plan`, while `review_cli.py`
dispatches the sources supported by `meow review`. `issue_solver.py` handles
the Jira build flow; `gitlab_reviewer.py` and `branch_reviewer.py` provide
review-source helpers; `lint_fix.py` implements standalone lint-fix mode.

## Modules

| Area | Modules | Responsibility |
|---|---|---|
| Project and sprint state | `config.py`, `sprint.py` | Load the shared/local MEOW config and assemble shared workflow state. |
| Shared workflow support | `lint.py`, `worktree.py`, `rules.py`, `logging.py`, `plan_files.py` | Lint hooks and checks, worktree setup, project rules, logging, and plan/review file lookup. |
| Agents | `agents/` | Explorer, planner, generator, reviewer, and fixer roles. |
| Commands and orchestration | `cli.py`, `sprint_runner.py`, `review_cli.py`, `orchestrator.py` | Command-line dispatch and the shared execution loops. |
| Integrations and review sources | `issue_solver.py`, `gitlab_reviewer.py`, `branch_reviewer.py` | Jira runs and GitLab or branch review support. |
| Native skill support | `prompts.py`, `native*.py` | Shared role prompts and deterministic helpers used by in-session skill execution. |

The package uses a `src/` layout intentionally. Running from the repository
root reaches the installed package, so a broken editable installation is not
masked by importing source files directly.

## Agent contracts

Each agent role is a `*Agent(context)` class based on `agents/base.py`. The
`AgentContext` protocol provides role model selection, the working directory,
and lint commands. `Sprint` satisfies both `AgentContext` and the narrower
`GeneratorContext`; the sprint-free `ProjectContext` satisfies
`AgentContext` for review operations that do not need a sprint or plan.

The shared `Agent` base builds SDK options and runs one-shot queries. The
explorer stays declarative and returns an `AgentDefinition` for nested use.
The generator keeps a persistent `ClaudeSDKClient` across feedback rounds.
Thin function-shaped wrappers remain for compatibility with existing imports.

## Native and CLI execution

The CLI uses the Agent SDK to run agent roles. Native skill mode runs planning
and generation in the calling session and dispatches a fresh reviewer for
each round. Both modes share the role prompts from `prompts.py`, read the same
configuration, and use the same file and verdict formats. `native.py` re-exports
deterministic helpers implemented in `native_prepare.py`, `native_lint.py`,
`native_state.py`, and `native_prompt.py`; `native_cli.py` exposes them through
the `meow native` command.

See [shared skill protocol](skills/_shared/native-mode.md) for more detail.

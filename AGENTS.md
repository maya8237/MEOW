# AGENTS.md

MEOW — Management, Execution & Optimization of Workflows.

## Harness

Feature work in this repository runs through the MEOW harness. From the repo
root, with `.venv` active:

```bash
meow run "<feature description>" --name "<feature-name>"
```

`meow plan "<feature description>" --name "<feature-name>"` writes the sprint
plan only. See [docs/CLI.md](docs/CLI.md) for full command behavior.

- **Jira:** `meow run --jira [ISSUE-KEY]` fetches an issue and runs in its own
  worktree, committing and pushing after verification. It requires `[jira]`/`[jira.mcp]` and
  rejects `--name`, `--no-worktree`, `--source-branch`, `--resume-at`, and
  `--plan-file`.
- **Lint:** `meow run --lint-fix` runs configured linters and fixes findings.
  `--report-only` reports without editing; the `lint-fix` skill uses this mode.
  Lint-fix takes no request text, Jira, or worktree options.
- **Delivery:** A verified feature run in a separate linked worktree commits
  and pushes to `origin`. `run --unattended` enables this for an in-place run.
  Delivery failures retain the worktree and run checkpoint.
- **Source branch:** `run` and `plan --source-branch BRANCH` (also `--from` or
  `-b`) create a worktree from that branch. The uncommitted-changes check is
  skipped only when creating a worktree from an explicit source branch.
- **Plan approval:** `run --manually-approve-plan` (also `-m`) prints the plan
  and prompts before generation. `plan` does not accept it. Do not use it on
  scheduled Jira runs because no one can answer the prompt.
- **Resume:** `run --resume-at {generate,review}` defaults to `generate`.
  `review` skips planning, uses `--plan-file` or the latest plan in `docs_dir`
  (default `.meow/plans`),
  and reviews existing code before invoking the generator. `plan`, Jira runs,
  and lint-fix mode do not accept this option.

`meow review` supports prompt, Jira, GitLab, branch-diff, plan-file, and
review-file sources. It is report-only by default; `--fix` loops review and
fixes up to `max_rounds`. Exactly one source may be given. With no source, it
uses the latest plan in `docs_dir` (default `.meow/plans`) or falls back to a
prompt/diff review.

- `--gitlab` requires `[gitlab.mcp]` and is always read-only; it cannot combine
  with `--fix`.
- `--jira` requires `[jira]`/`[jira.mcp]` and reviews current code against the
  fetched issue.
- `--branch BRANCH --target TARGET` reviews a local branch diff without GitLab
  MCP. It uses a worktree by default; `--no-worktree` requires the selected
  directory to already be on that branch.
- `--plan-file PATH` checks implementation against the plan's Sprint Contract.
- `--review-file PATH` resumes a prompt- or plan-based review. GitLab and
  branch reviews must be rerun from their source.

Every command accepts `--working-dir PATH` (also `--work-dir` or `-d`). Shared
config lives in `.meow/config.toml`; `.meow/config.local.toml` has highest
priority, followed by the project config and user fallback
`~/.meow/config.toml`; plans, contracts, reviews, runs, and evidence live
under `.meow/` while project documentation stays in `docs/`; only
MEOW-generated plans belong in `.meow/plans/`. Integration
configuration is in
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

To onboard another repo, use `/meow:onboard`. It sets up a current/clean
project and repairs its `.gitignore` itself. Use `/meow:migration` separately
for legacy `.harness.toml` layouts; migration also repairs `.gitignore` itself.
Both skills ask about optional integrations and project features using yes/no
choices.

## Native (in-session) skill execution

Each skill runs in one of two modes. **Native** (the default in Claude Code):
the calling session plans and generates, and a fresh Task subagent reviews
each round. The agent-free `meow native` helpers provide deterministic facts
as JSON. **CLI** mode shells out to `meow <command>` and uses the Agent SDK;
this is the headless path.

Both modes share `.meow/config.toml` semantics, file names, and the
`SUMMARY:`/`STATUS:` verdict format. The shared protocol is
[skills/_shared/native-mode.md](skills/_shared/native-mode.md). Role prompts
are defined in `src/meow/prompts.py`; update them there, not in a skill.

Claude owns transcript retention. MEOW stores only role-to-session references in
the atomic run JSON needed for resume; it does not copy Claude events into a
second transcript database.
Design: [native execution spec](docs/superpowers/specs/2026-09-29-native-skill-execution-design.md).

## Claude Code plugin

This repository is also a Claude Code plugin. It provides `/meow:run`,
`/meow:plan`, `/meow:review`, and `/meow:lint` skills. Each wraps the matching
CLI command except `/meow:lint`, which runs `meow run --lint-fix --report-only`
and fixes findings in the calling session. See `skills/*/SKILL.md`.

## Lint

`ruff check` is the project-wide gate, configured in `.meow/config.toml`. The
harness appends `--fix` when linting individual files touched by the generator;
the reviewer runs the configured gate unmodified. Rule selection is in
`pyproject.toml`.

Projects may declare multiple `[[lint]]` commands. See
`templates/meow-config.toml.example` for `per_file` and `gate` fields. MEOW itself
uses one command.

## Layout

The engine is split by responsibility under `src/meow/`:

- `config.py`, `sprint.py`, `lint.py`, `test_runner.py`, `worktree.py`, `logging.py`,
  and `plan_files.py` handle project settings and shared workflow support.
- `agents/` contains the explorer, planner, generator, reviewer, and fixer
  roles, built on the shared agent base.
- `orchestrator.py` owns the generator/reviewer round loops; `sprint_runner.py`
  and `review_cli.py` implement the top-level command flows.
- `issue_solver.py`, `gitlab_reviewer.py`, and `branch_reviewer.py` support
  Jira, GitLab, and branch reviews; `lint_fix.py` implements lint-fix mode.
- `prompts.py` supplies role prompts to both execution modes. `native_*.py`,
  `native.py`, and `native_cli.py` provide deterministic helpers for native
  skill execution.
- `cli.py` wires up the `meow` console command.

The `src/` layout is deliberate: code run from the repository root reaches the
installed copy, so a broken editable install is caught rather than masked.

## Agent structure

Every role in `agents/` follows the `*Agent(context)` pattern from
`agents/base.py`. `Sprint` and the sprint-free `ProjectContext` both satisfy
`AgentContext`; `Sprint` also satisfies `GeneratorContext`, which includes the
generator's explorer definition and lint hook.

The shared `Agent` base builds SDK options and runs one-shot queries. The
explorer returns an `AgentDefinition` for nested use, while the generator
keeps a persistent `ClaudeSDKClient` across feedback rounds. Thin function
wrappers remain for compatibility with existing imports.


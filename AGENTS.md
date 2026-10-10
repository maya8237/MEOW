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
  rejects `--name`, `--no-worktree`, `--from`, `--resume-at`, and
  `--plan`.
- **Lint:** `meow run --lint-fix` runs configured linters and fixes findings.
  `--report-only` reports without editing; the `lint-fix` skill uses this mode.
  Lint-fix takes no request text, Jira, or worktree options.
- **Delivery:** A verified feature run in a separate linked worktree commits
  and pushes to `origin`. `run --unattended` allows an isolated feature run
  to deliver without interactive handoff and enables `--background`; it cannot
  be combined with `--no-worktree`, manual plan approval, `--from`,
  `--resume-at review`, or lint-fix mode. Delivery failures retain the
  worktree and run checkpoint.
- **Source branch:** `run` and `plan --from BRANCH` (also
  `-b`) create a worktree from that branch. The uncommitted-changes check is
  skipped only when creating a worktree from an explicit source branch.
- **Plan approval:** `run --manually-approve-plan` (also `-m`) prints the plan
  and prompts before generation. `plan` does not accept it. Do not use it on
  scheduled Jira runs because no one can answer the prompt.
- **Resume:** `run --resume-at {generate,review}` defaults to `generate`.
  `review` skips planning, uses `--plan` or the latest plan in `docs_dir`
  (default `.meow/plans`),
  and reviews existing code before invoking the generator. `plan`, Jira runs,
  and lint-fix mode do not accept this option.

`meow review` supports prompt, Jira, GitLab, GitHub, branch-diff, plan,
review-file, and GitLab CI sources. It is report-only by default; `--fix`
loops review and fixes up to `max_rounds`. Exactly one source may be given.
With no source, it uses the latest plan in `docs_dir` (default `.meow/plans`)
or falls back to a prompt/diff review.

- `--gitlab` requires `[gitlab.mcp]` and is always read-only; it cannot combine
  with `--fix`.
- `--github` requires `[github.mcp]` and is always read-only; it cannot combine
  with `--fix`.
- `--jira` requires `[jira]`/`[jira.mcp]` and reviews current code against the
  fetched issue.
- `--ci` reviews the exact GitLab pipeline checkout and is report-only; it does
  not use an MCP server or a local worktree.
- `--branch BRANCH --target TARGET` reviews a local branch diff without GitLab
  MCP. It uses a worktree by default; `--no-worktree` requires the selected
  directory to already be on that branch.
- `--plan PATH` checks implementation against the plan's Sprint Contract.
- `--review-file PATH` resumes a prompt- or plan-based review. GitLab, GitHub,
  and branch reviews must be rerun from their source.

Every command accepts `--work-dir PATH` (also `-d`). Shared
config lives in `.meow/config.toml`; `.meow/config.local.toml` has highest
priority, followed by the project config and user fallback
`~/.meow/config.toml`; plans, contracts, reviews, runs, and evidence live
under `.meow/` while project documentation stays in `docs/`; only
MEOW-generated plans belong in `.meow/plans/`. Integration
configuration is in
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

A project is set up automatically on its first config-dependent command (a
minimal `.meow/config.toml`, a documentation-only local template, and the
`.gitignore` boundary; see [docs/CLI.md](docs/CLI.md)). `/meow:onboard` is
for add-ons on an onboarded project and can also run the full setup flow for a
project that has never been onboarded. It repairs its `.gitignore` itself.
Use `/meow:migration` separately for legacy layouts; migration also repairs
`.gitignore` itself. Both skills ask about optional integrations and project
features using yes/no choices.

## Native (in-session) skill execution

The workflow skills `run`, `plan`, `review`, and `lint` document two execution
paths. `run` defaults to the CLI so automatic project understanding and
checkpointing stay in one process; `plan`, `review`, and `lint` default to the
native Claude Code session when invoked there. The agent-free `meow native`
helpers provide deterministic facts as JSON. The `onboard`, `migration`, and
`customize` skills are guidance flows that use the documented helpers rather
than separate CLI workflows.

Both modes share `.meow/config.toml` semantics, file names, and the
`SUMMARY:`/`STATUS:` verdict format. The shared protocol is
[skills/_shared/native-mode.md](skills/_shared/native-mode.md). Role prompts
are defined in `src/meow/prompts.py`; update them there, not in a skill.

Claude owns transcript retention. MEOW stores only role-to-session references in
the atomic run JSON needed for resume; it does not copy Claude events into a
second transcript database.

## Claude Code plugin

This repository is also a Claude Code plugin. It provides workflow skills
`/meow:run`, `/meow:plan`, `/meow:review`, and `/meow:lint`, plus the
`/meow:onboard`, `/meow:migration`, and `/meow:customize` guidance skills.
The workflow wrappers are documented in `skills/*/SKILL.md`; `/meow:lint`
reports through `meow run --lint-fix --report-only` and fixes findings in the
calling session.

## Lint

`ruff check` is the project-wide gate, configured in `.meow/config.toml`. The
harness appends `--fix` when linting individual files touched by the generator;
the reviewer runs the configured gate unmodified. Rule selection is in
`pyproject.toml`.

Projects may declare multiple `[[lint]]` commands. See
`templates/meow-config.toml.example` for `per_file` and `gate` fields. MEOW itself
uses one command.

## Layout

The engine is split by responsibility under `src/meow/`. Keep the package root
limited to package entry points; implementation belongs in the narrowest
responsibility package:

- `agents/` contains the explorer, planner, generator, reviewer, tester,
  fixer, documentation, and integration-fetcher roles.
- `cli/` owns argument parsing, dispatch, status, resume, queue, IPython, and
  review command entry points.
- `execution/` owns sprint orchestration, run state, delivery, cancellation
  policy, plan approval, and queue execution.
- `infrastructure/` owns linting, test/build checks, logging, usage, background
  workers, worktree lifecycle, worktree setup, and quality evidence.
- `integrations/` owns Jira, GitLab, GitHub, CI review, documentation update,
  and project-knowledge adapters.
- `native/` provides deterministic helpers and the JSON CLI used by native
  skill execution.
- `project/` owns configuration, onboarding, plan files/state, shaping,
  prompts, permissions, and command policy.
- `hooks/` owns reversible Claude Code hook installation and handlers;
  `installer/` owns the post-install bootstrap; `tasks/` owns task-graph and
  scheduler support.
- `frontend.py` provides browser-capability discovery and `evaluation.py`
  reports saved run quality.

Prefer creating or reusing subpackages for related modules instead of adding
many floating files to a package. Two to four directly owned files can make
sense when the boundary is clear, but this is a guideline rather than a hard
limit. Empty placeholder packages are not kept; see
`docs/ARCHITECTURE.md` for the full package-ownership guidance.

The `src/` layout is deliberate: code run from the repository root reaches the
installed copy, so a broken editable install is caught rather than masked.

## Agent structure

Every role in `agents/` follows the `*Agent(context)` pattern from
`agents/base.py`. `Sprint` and the sprint-free `ProjectContext` both satisfy
`AgentContext`; `Sprint` also satisfies `GeneratorContext`, which includes the
generator's explorer definition and lint hook.

The shared `Agent` base builds SDK options and runs one-shot queries. The
explorer returns an `AgentDefinition` for nested use, while the generator and
the review/lint fixers keep persistent `ClaudeSDKClient` sessions across
feedback rounds. Integration fetchers are read-only and feed structured
source material into the corresponding workflow. Internal callers use the
canonical agent classes and responsibility packages directly.


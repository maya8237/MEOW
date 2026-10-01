# CLI guide

This page covers the detailed behavior of MEOW's command-line interface.
For installation and a quick start, see the [README](../README.md). For Jira
and GitLab configuration, scheduled runs, and error details, see
[INTEGRATIONS.md](INTEGRATIONS.md).

## Common options

Every command accepts `--working-dir PATH` (also `--work-dir` or `-d`) to
select a project directory. Sprint plans and reviews are written under that
project's configured `docs_dir`.

`run` and `plan` use a worktree by default. `--no-worktree` runs in the
selected directory instead; in that mode, the feature name is optional.

## Build and plan

```bash
meow run "Add CSV export" --name "add-csv-export"
meow plan "Add CSV export" --name "add-csv-export"
```

`run` plans, implements, and reviews the feature. `plan` writes only the plan.
Both accept `--source-branch BRANCH` (also `--from` or `-b`) to create a
worktree from a specific branch. Existing worktrees are reused by name and
take priority over a new source branch.

`run --manually-approve-plan` (also `-m`) displays the plan and waits for
approval before implementation. Declining exits without starting the
generator. `plan` does not accept this option because it never implements.

To continue an interrupted run, use `--resume-at review`. MEOW skips planning
and reviews the current code first, using `--plan-file PATH` if supplied or
the latest plan in `docs_dir` otherwise. If that review finds work, the
generator continues from there. The default, `--resume-at generate`, starts
with a fresh plan unless `--plan-file` is supplied.

### Jira issue run

```bash
meow run --jira PROJ-123
meow run --jira
```

This fetches the selected issue (or the latest issue in the configured Jira
project), runs the sprint in its own worktree, and pushes the passing branch
to `origin`. It prints a JSON result on success. Configure `[jira]` and
`[jira.mcp]` as described in [INTEGRATIONS.md](INTEGRATIONS.md).

The Jira mode always creates its own worktree and branch, so it rejects
`--name`, `--no-worktree`, `--source-branch`, `--resume-at`, and `--plan-file`.
It accepts `--manually-approve-plan` for interactive runs; do not use that
option for unattended scheduled runs.

### Lint fix

```bash
meow run --lint-fix
meow run --lint-fix --report-only
```

The default mode runs configured auto-fix commands and sends remaining
findings to a fixer agent until clean or `max_rounds` is reached. It edits the
selected directory in place and requires a clean working tree.

`--report-only` prints lint output without editing files or starting an agent.
It is intended for the `/meow:lint` skill, which performs fixes in the
calling session.

## Review

```bash
meow review
meow review "Check API error handling"
meow review --jira PROJ-123
meow review --gitlab "https://gitlab.example.com/group/project/-/merge_requests/123"
meow review --branch feature/add-csv-export --target main
meow review --plan-file docs/exec-plans/active/add-csv-export.md
```

With no source, review uses the latest plan or falls back to a code-diff
review. Other sources are a free-text prompt, Jira issue, GitLab merge
request, local branch diff, or specific plan file. Only one source may be
selected.

Reviews are report-only by default: one reviewer pass writes a PASS/FAIL
verdict without editing. Add `--fix` to loop through review, fixes, and
another review up to `max_rounds`. This option is supported for all sources
except `--gitlab`, which has no local checkout to edit.

Branch reviews require `--target TARGET` and use an isolated worktree by
default. Add `--no-worktree` to fix in place, but the selected directory must
already be on the reviewed branch. Other review sources use the selected
working directory directly and do not require a clean tree.

`--review-file PATH` (also `-r`) resumes fixing an existing prompt- or
plan-based review. Pass the original prompt when resuming a prompt-based
review. GitLab and branch reviews cannot be resumed this way; rerun the
original source instead. Review files are saved under `docs_dir`, with names
based on their source (`review.md`, `gitlab-review.md`, `branch-review.md`,
or `<plan>-review.md`).

Configure `[jira]`/`[jira.mcp]` for `--jira` or `[gitlab.mcp]` for `--gitlab`.
See [INTEGRATIONS.md](INTEGRATIONS.md).

## Native and CLI skill modes

MEOW's Claude Code skills use native mode by default: the calling session
plans and generates, while a fresh subagent reviews each round. Jira and
GitLab access comes from the MCP tools connected to that session. Ask for
"CLI mode" (or headless mode) to run the `meow` CLI instead.

Both modes read the same `.harness.toml`, use the same worktree rules and
`max_rounds`, and write the same plans, reviews, and verdict format. The CLI
is the option for unattended terminal or scheduled runs. See the shared
[native mode protocol](../skills/_shared/native-mode.md).

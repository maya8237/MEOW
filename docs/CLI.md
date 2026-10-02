# CLI guide

This page covers the detailed behavior of MEOW's command-line interface.
For installation and a quick start, see the [README](../README.md). For Jira
and GitLab configuration, scheduled runs, and error details, see
[INTEGRATIONS.md](INTEGRATIONS.md).

## Project understanding

`meow knowledge audit` prints an evidence-backed, report-only JSON audit.
`meow knowledge check` reports deterministic structural failures separately
from advisory prose drift. `meow knowledge create --finding ID` is a separate,
explicit action and preserves existing documents by default.

`meow shape assess "request"` gives an optional shaping recommendation. Clear
requests continue directly to planning; accepted shape/breadboard artifacts
are advisory inputs.

Use the knowledge commands when onboarding or auditing an unfamiliar project,
or when shared architecture/domain/security/reliability guidance may be
missing. Use `shape assess` when a request is broad, ambiguous, cross-component,
or has multiple viable approaches. These workflows are optional: precise
requests go directly to `meow plan` or `meow run`, and neither workflow is an
automatic gate.

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

After required checks and review pass, a feature run in a separate linked
worktree commits and pushes its branch to `origin`. A detached feature worktree
receives a `meow/RUN_ID` branch at delivery. An in-place run does this only
with `--unattended`. A missing remote or failed push leaves the worktree and
checkpoint for recovery. `--unattended` cannot be combined with manual plan
approval or lint-fix mode.

`run --manually-approve-plan` (also `-m`) displays the plan and waits for
approval before implementation. Declining exits without starting the
generator. `plan` does not accept this option because it never implements.

To continue an older plan without a saved run, use `--resume-at review`. MEOW skips planning
and reviews the current code first, using `--plan-file PATH` if supplied or
the latest plan in `docs_dir` otherwise. If that review finds work, the
generator continues from there. The default, `--resume-at generate`, starts
with a fresh plan unless `--plan-file` is supplied.

### Run status and recovery

Each `meow run` saves an atomic checkpoint under `.meow/runs/`. The directory
is ignored by Git. Inspect the latest run or a specific ID without starting
agents:

```bash
meow status
meow status RUN_ID --verbose
meow resume RUN_ID
meow resume RUN_ID --continue
meow resume RUN_ID --auto-resume
```

`resume` inspects and exits by default. Both continuation flags validate the
saved repository, worktree, branch, plan, and configuration before starting
agents. A run interrupted during a possible edit reviews the saved worktree
against its plan before any generator retry. A mismatch stops with a recovery
diagnostic. Failed and interrupted runs retain their worktree and evidence.
Completion requires an independent reviewer PASS, an enabled tester PASS, and
current passing required lint, test, and build checks. Advisory failures remain
visible. Missing SDK usage is shown as `unavailable`.

### Jira issue run

```bash
meow run --jira PROJ-123
meow run --jira
```

This fetches the selected issue (or the latest issue in the configured Jira
project), runs the sprint in its own worktree, commits and pushes the passing
branch, and prints a JSON result on success. Configure `[jira]` and
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
# GitLab CI review

`meow review --ci` reviews the exact GitLab pipeline checkout against the
fetched `refs/remotes/origin/dev` commit. It accepts a detached source checkout
when `HEAD` matches `CI_COMMIT_SHA`. The review is report-only: it does not
plan, fix, commit, push, or open a merge request. It uses the local Git history,
so GitLab MCP is unnecessary.

The root `.gitlab-ci.yml` includes [the GitLab review job](../templates/gitlab-ci-review.yml)
for detached merge request pipelines targeting `dev` and non-`dev` branch push pipelines. In merge request pipelines, the reviewer reads the MR description as the review brief; GitLab descriptions longer than 2,700 characters are truncated and flagged. Push pipelines have no MR brief and review the diff alone. The job fetches `dev` without moving HEAD,
uses full Git history for a reliable merge base, and uploads
`.meow-ci-artifacts/review.md` and `.meow-ci-artifacts/verdict.json` even when
the job fails. Provide the Claude Agent SDK credentials through masked CI
variables. The job must install this project and fetch the target ref before
running the command. Merged-result, tag, and other pipeline types are rejected.

The command also accepts `--target-ref REF` for controlled use,
`--artifact-dir PATH`, and an existing `--plan-file PATH`. PASS exits 0, FAIL
exits 1, and an unverified or infrastructure failure exits 2. The artifacts
record the source and target SHAs, merge base, verdict, and failure reason.
No local branch checkout or worktree is required.

# CLI guide

This page covers the detailed behavior of MEOW's command-line interface.
For installation and a quick start, see the [README](../README.md). For Jira
and GitLab/GitHub configuration, scheduled runs, and error details, see
[INTEGRATIONS.md](INTEGRATIONS.md).

## User commands

These are the commands meant for normal project work:

| Command | Use it when you want to |
| --- | --- |
| `meow run` | Plan, implement, verify, review, and optionally deliver a feature request. |
| `meow review` | Review existing code, and optionally fix findings with `--fix`. |
| `meow plan` | Write the sprint plan only, without implementing it. |
| `meow status` | Inspect a saved or background run. |
| `meow cancel` | Ask a running sprint to stop cooperatively. |
| `meow worktree` | Inspect or safely clean MEOW-owned run worktrees. |
| `meow resume` | Inspect or continue a saved run. |
| `meow queue` | Enqueue tasks or run queued tasks serially in the current repository. |
| `meow hooks` | Manage optional host hooks after reviewing what they install. |
| `meow ipython` | Open the interactive MEOW IPython session. |

`mw` is a shorter alias for the `meow` binary, and each command has a
single-letter shortcut: `r` run, `p` plan, `rv` review, `l` lint (`run
--lint-fix`), `s` status, `x` cancel, `c` resume, `q` queue, `w` worktree,
`h` hooks, `i` ipython. For example, `mw r "Add CSV export" --name csv-export`.

Running `meow` without a command or `meow ipython` opens an IPython session. Inside it, the
top-level MEOW commands are available as bare commands, for example
`status` and `native verify`. `run` is not bare, to keep IPython's built-in `%run`; use
`meow run "Add CSV export" --name csv-export`.
The explicit `%meow ...` magic and regular `!meow ...` shell form are also
available.

### Queueing work

Use `meow queue "request"` to persist work for the current repository. This
works while another MEOW run is active or while the repository is idle. Run
`meow queue` without a request to process pending items in FIFO order. A
repository-scoped worker lock ensures that two sessions cannot process the
same queue concurrently; a failed task pauses the queue for inspection and
retry.

## Optional editor hooks

`meow hooks status claude` reports `active`, `missing`, or `modified` for each
MEOW Claude Code hook by comparing the local installation manifest with Claude
settings. It only reads files. `meow hooks install claude --only lint` installs
an accepted subset; repeat `--only` for more hooks. Onboarding shows each hook's
host event, exact command, and effect before installation. Editor hooks are
optional: `meow run`, including `--unattended`, works without them.

Use `meow hooks uninstall claude` to remove hooks recorded by MEOW's manifest.
The supported hook names are `lint`, `shaping`, `plan_capture`, and `plan_stop`.
Uninstall only removes the MEOW-owned entries; it does not rewrite unrelated
Claude settings.

## Quality concerns

Reviewers may record concrete maintenance concerns with a repository path,
exact code evidence, impact, and a suggested follow-up. MEOW deduplicates them
across review rounds and displays only live concerns observed in the current
run in `meow status`. `meow status --verbose` also shows their evidence and
follow-up. Concerns are advisory and do not independently fail a verification
gate. MEOW does not calculate a numerical project quality score.

## Verification preflight and browser checks

Onboarding validates each proposed lint, test, and build command's executable
and configured `cwd` before saving it. Running a proposed command is a separate
preflight step after the user reviews its exact command and possible effects;
preflight reports timeout, failure, and files changed by the command.

When `[tester.browser]` uses `kind = "command"`, MEOW starts the configured
`[[tester.dev_server]]`, waits for its readiness URL, runs the project's browser
test command, records named flows and changed artifact paths, then stops the
server. A required browser failure or unavailable provider blocks completion.
`meow status` shows separate lint, test, build, and browser results. Browser
artifacts should live under `.meow/` so verification output does not change the
code revision being checked.

## Project understanding

`meow run` and `meow plan` inspect project guidance and code before planning.
When a request needs design choices, they shape the requirements and may map
complex UI or cross-component work as a breadboard. Clear requests proceed
directly to planning. These phases run automatically and require no separate
command. An unresolved product choice stops with a checkpoint, including with
`--unattended`; unattended runs never wait for input. Feature runs do not
suggest or edit prose documentation. Onboarding uses the same project audit
internally and can create selected knowledge documents as part of setup.

## Common options

Every public command accepts `--work-dir PATH` (also `-d`) to select a project
directory. Native helper commands accept the same path as `--working-dir` (and
also accept `--work-dir`/`-d`). By default, MEOW-generated plans and reviews
are written under `.meow/plans/`; an explicit `docs_dir` remains supported for
existing projects. Project documentation stays under `docs/`.

`run` and `plan` use a worktree by default. `--no-worktree` runs in the
selected directory instead; in that mode, the feature name is optional.

## Build and plan

```bash
meow run "Add CSV export" --name "add-csv-export"
meow plan "Add CSV export" --name "add-csv-export"
```

`run` plans, implements, and reviews the feature. `plan` writes only the plan.
Both accept `--from BRANCH` (also `-b`) to create a
worktree from a specific branch. A run that plans from scratch always gets its
own worktree: if `.worktrees/NAME` exists it uses `NAME-2`, `NAME-3`, and so
on, and plan, review, and test files take that name. Runs that continue
existing work (`--plan`, `--resume-at review`, `meow resume`) reuse the
existing worktree and take priority over a new source branch.

`run` (including `--jira` and `--lint-fix` without `--report-only`) refuses to
start while the checkout has uncommitted changes other than `.gitignore` and
`.meow/`, unless it creates a worktree from an explicit `--from` branch.
`plan` and `review` never require a clean tree.

On a project that was never onboarded, the first run onboards it automatically
before planning: it repairs the `.gitignore` boundary (`.meow/*`, `!.meow/`,
`!.meow/config.toml`) and writes a minimal `.meow/config.toml` with the lint
command it detects (ruff or eslint). Nothing is prompted and there is no
opt-out flag. The setup happens in the run's own checkout, so it is delivered
with the feature from a linked worktree and left as local changes for an
in-place run. `plan`, `review`, `run --lint-fix`, queued runs, and Jira runs
onboard the same way; onboarding never overwrites an existing config and an
onboarding failure never fails the command. `/meow:onboard` is for add-ons.

After required checks and review pass, a feature run in a separate linked
worktree commits and pushes its branch to `origin`. A detached feature worktree
receives a `meow/RUN_ID` branch at delivery. `--unattended` opts into the same
non-interactive delivery path and is required for `--background`; it still
requires an isolated worktree. An in-place `--no-worktree` run does not
automatically commit or push. A missing remote or failed push leaves the
worktree and checkpoint for recovery. `--unattended` cannot be combined with
`--no-worktree`, manual plan approval, a source branch, resume-at-review, or
lint-fix mode.

`run --manually-approve-plan` (also `-m`) displays the plan and waits for
approval before implementation. Declining exits without starting the
generator. `plan` does not accept this option because it never implements.

To continue an older plan without a saved run, use `--resume-at review`. MEOW skips planning
and reviews the current code first, using `--plan PATH` if supplied or
the latest plan in `docs_dir` otherwise. If that review finds work, the
generator continues from there. The default, `--resume-at generate`, starts
with a fresh plan unless `--plan` is supplied.

### Run status and recovery

Each `meow run` saves an atomic checkpoint under `.meow/runs/`. The directory
is ignored by Git. Inspect the latest run or a specific ID without starting
agents:

```bash
meow status
meow status RUN_ID --verbose
meow resume RUN_ID
meow resume RUN_ID --continue
```

`resume` inspects and exits by default. `--continue` validates the saved
repository, worktree, branch, plan, and configuration before starting agents.
A run interrupted during a possible edit reviews the saved worktree against
its plan before any generator retry. A mismatch stops with a recovery
diagnostic. Failed and interrupted runs retain their worktree and evidence.
Completion requires an independent reviewer PASS, an enabled tester PASS, and
current passing required lint, test, and build checks. Advisory failures remain
visible. Status reports SDK turns, tokens, and USD cost accumulated from received agent results. The verbose view lists each result by role. Missing SDK metrics are shown as `unavailable`.

Claude owns the conversation transcript and its retention. MEOW keeps only the
small role-to-session-ID references needed to resume the generator or reviewer
in the atomic run JSON; it does not duplicate Claude's transcript or event
store.

`meow cancel RUN_ID` requests cancellation of an active run. The runner checks
the request between phases and while awaiting agents or verification commands.
It stops those tasks, keeps the worktree and checkpoint, and records a
`cancelled` phase. Resume explicitly with `meow resume RUN_ID --continue`;
resumption validates the checkout and reviews existing edits first when a
generator had started. Cancellation does not signal an unrelated process ID.

Use `meow run "REQUEST" --name NAME --unattended --background` to detach a
local feature run from its launching terminal. The command returns a run ID
immediately. `meow status RUN_ID` shows the worker state and its run-owned log
path; `meow cancel RUN_ID` requests a cooperative stop. If a worker disappears,
status marks the run interrupted and shows the resume command. Background
execution requires `--unattended` and cannot wait for plan approval.

An optional `[background]` table can set `notify_command` to a literal
argument array, for example `notify_command = ["notify-meow"]`. MEOW appends
only the run ID, terminal phase, and status command. Notification failure is
recorded separately and cannot change verification results.

Inspect saved run worktrees with `meow worktree list` and
`meow worktree inspect RUN_ID`. `meow worktree clean RUN_ID` removes only a
completed, registered MEOW worktree inside the repository's `.worktrees`
directory when it is clean and its branch has no unpushed commits. A detached
branch or unknown push state blocks cleanup. There is no force cleanup flag.

Onboarding can configure `[worktree_setup]` with selected regular files and
literal command argument arrays. Setup runs once for a newly created feature
worktree before planning; each completed action is recorded in the run
checkpoint. Paths containing `..`, links, and secret-like file names are
rejected. Failed setup retains the worktree for inspection.
Each configured command is approved as an exact argument array during
onboarding. A `[permissions]` rule for the `setup` role and `WorktreeSetup`
tool can further deny it or require approval; unattended runs stop in either
case.

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
`--name`, `--no-worktree`, `--from`, `--resume-at`, and `--plan`.
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
meow review --github "https://github.com/group/project/pull/123"
meow review --ci
meow review --branch feature/add-csv-export --target main
meow review --plan .meow/plans/add-csv-export.md
```

With no source, review uses the latest plan or falls back to a code-diff
review. Other sources are a free-text prompt, Jira issue, GitLab merge request,
GitHub pull request, local branch diff, or specific plan file. Only one source
may be selected.

`--ci` reviews the exact detached GitLab pipeline checkout against the fetched
target ref. It is report-only and cannot be combined with another source,
`--fix`, `--test`, or `--no-worktree`; see [GitLab CI review](#gitlab-ci-review)
for the pipeline contract.

Reviews are report-only by default: one reviewer pass writes a PASS/FAIL
verdict without editing. Add `--fix` to loop through review, fixes, and
another review up to `max_rounds`. This option is supported for all sources
except `--gitlab` and `--github`, which have no local checkout to edit.

Branch reviews require `--target TARGET` and use an isolated worktree by
default. Add `--no-worktree` to fix in place, but the selected directory must
already be on the reviewed branch. Other review sources use the selected
working directory directly and do not require a clean tree.

`--review-file PATH` (also `-r`) resumes fixing an existing prompt- or
plan-based review. Pass the original prompt when resuming a prompt-based
review. GitLab, GitHub, and branch reviews cannot be resumed this way; rerun the
original source instead. Review files are saved under the configured plan
directory, which defaults to `.meow/plans/`, with names based on their source:
`<plan>-review.md` for plan reviews, and `prompt.<id>.review.md`,
`gitlab.<id>.review.md`, `github.<id>.review.md` or `branch.<id>.review.md`
(a random 8-hex `<id>`, so concurrent reviews never overwrite each other).
Older fixed names such as `review.md` still resume.

Configure `[jira]`/`[jira.mcp]` for `--jira`, `[gitlab.mcp]` for `--gitlab`, or
`[github.mcp]` for `--github`.
See [INTEGRATIONS.md](INTEGRATIONS.md).

## Native and CLI skill modes

The workflow skills `/meow:plan`, `/meow:review`, and `/meow:lint` use native
mode by default: the calling session plans or fixes, while a fresh subagent
reviews each round where applicable. `/meow:run` uses CLI mode by default;
native run is an explicit opt-in. Jira, GitLab, and GitHub access in native
mode comes from MCP tools connected to that session. Ask for "CLI mode" (or
headless mode) when a native-default skill should run the `meow` CLI instead.
`/meow:onboard`, `/meow:migration`, and `/meow:customize` are guidance flows
that configure or explain the system rather than alternate execution engines.

Both modes read the same active MEOW configuration, use the same worktree rules
and `max_rounds`, and write the same plans, reviews, and verdict format. The
shareable project configuration is `.meow/config.toml`. Configuration priority
is `.meow/config.local.toml`, project `.meow/config.toml`, user
`~/.meow/config.toml`, then built-in defaults. `agent_skills.default` and
`agent_skills.<role>` are the exception: their lists append across all config
layers. The CLI is the option for
unattended terminal or scheduled runs. See the shared
[native mode protocol](../skills/_shared/native-mode.md).

## Maintainer and internal commands

These commands are hidden from `meow --help` because they are not the normal
user surface.

`meow docs-update` is for maintainers updating MEOW's own prose docs on a
clean `dev` checkout. Run `meow docs-update --since REF` the first time, using
the commit from which documentation should be reviewed. Later, run
`meow docs-update` after the previous documentation changes and
`docs/.meow-docs-update.json` have been committed. It compares the saved
inspected commit with the current HEAD, updates relevant prose, and prints its
baseline, edited paths, and diff. Review the documentation and marker together,
then commit them yourself. It does not commit or push.

`meow native` is the JSON helper used by MEOW's in-session skills. Skill
authors and maintainers may call it while debugging a skill flow, but normal
feature work should use the `/meow:*` skills or the public CLI commands above.

`meow evaluate` inspects saved run quality for harness maintainers. It is for
comparing or auditing MEOW runs, not for building, reviewing, or resuming
project work.

`meow _worker` is a private implementation detail used by
`meow run --unattended --background` to start the detached local worker. Humans
should not invoke it directly; use `meow status`, `meow cancel`, and
`meow resume` to interact with background runs.

## GitLab CI review

`meow review --ci` reviews the exact GitLab pipeline checkout against the
fetched `refs/remotes/origin/<target_branch>` commit (`[delivery].target_branch`,
default `dev`). It accepts a detached source checkout
when `HEAD` matches `CI_COMMIT_SHA`. The review is report-only: it does not
plan, fix, commit, push, or open a merge request. It uses the local Git history,
so GitLab MCP is unnecessary.

Include [the GitLab review job](../templates/gitlab-ci-review.yml) in a project's
pipeline for detached merge request pipelines targeting the target branch
and push pipelines on other branches; a merge request aimed at any other branch
is rejected. This repository ships the template; it does not require
a root `.gitlab-ci.yml`. In merge request pipelines, the reviewer reads the MR
description as the review brief; GitLab descriptions longer than 2,700
characters are truncated and flagged. Push pipelines have no MR brief and
review the diff alone. The job fetches the target branch without moving HEAD,
uses full Git history for a reliable merge base, and uploads
`.meow/ci-artifacts/review.md` and `.meow/ci-artifacts/verdict.json` even when
the job fails. Provide the Claude Agent SDK credentials through masked CI
variables. The job must install this project and fetch the target ref before
running the command. Merged-result, tag, and other pipeline types are rejected.

The command also accepts `--target-ref REF` for controlled use,
`--artifact-dir PATH`, and an existing `--plan PATH`. PASS exits 0, FAIL
exits 1, and an unverified or infrastructure failure exits 2. The artifacts
record the source and target SHAs, merge base, verdict, and failure reason.
No local branch checkout or worktree is required.

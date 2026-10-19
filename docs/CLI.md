# CLI guide

Use `meow COMMAND --help` for flag syntax. Read the relevant reference only:
[runs and recovery](RUNS.md), [orchestration](ORCHESTRATION.md),
[UI](UI.md), [configuration](INTEGRATIONS.md), or [workflow definitions](WORKFLOWS.md).

## User commands

| Command | Purpose |
|---|---|
| `meow run` | Plan, implement, verify, review, and deliver a feature |
| `meow plan` | Plan without implementation |
| `meow review` | Review existing code; `--fix` enables fixes |
| `meow status [ID]` | Inspect the latest or named run; `--verbose` includes evidence |
| `meow resume [ID]` | Inspect; `--continue` resumes, `--repair` re-baselines blocked gates |
| `meow cancel ID` | Cooperatively stop a run, preserving recovery state |
| `meow worktree` | List, inspect, or safely clean owned worktrees |
| `meow detach [--log FILE] -- CMD...` | Detach a command; returns PID and log JSON |
| `meow queue ["request"]` | Add a request, or process pending requests serially |
| `meow workflows` / `meow agents` | Inspect, eject, validate, or diff definitions |
| `meow ui` | Local catalog and session launcher |
| `meow schedule` | Add, list, edit, or cancel a one-shot trigger |
| `meow hooks` | Inspect/install/remove optional editor hooks |
| `meow ipython` | Interactive command session |

`mw` aliases `meow`. Shortcuts: `r` run, `p` plan,
`rv` review, `l` lint (`run --lint-fix`), `s` status, `x` cancel,
`c` resume, `q` queue, `w` worktree, `h` hooks, `i` ipython.

Bare `meow` opens IPython. Commands such as `status` work directly there;
use `meow run` to preserve IPython's `%run`. `%meow` and `!meow` also work.

## Common options

All public commands accept `--work-dir PATH` (`-d`); native helpers also accept
`--working-dir`. Artifacts default to `.meow/plans/` unless `docs_dir` is set.
Project documentation stays in `docs/`.

`meow --update` (`-u`) installs the latest published stable release, never
a branch tip; it cannot accompany a project command. Bundles include Python,
dependencies, UI, and plugin files and are checksum-verified. The installer
registers the plugin before switching launchers in `~/.meow/bin`; config,
caches, and earlier runtimes are retained. Failures return nonzero.
Keep runtimes used by workers/schedules. Rerun the installer for setup prompts
or migration from an editable install; remove obsolete launchers shadowing PATH.

Public commands warn on stderr about newer versions, silently ignoring network
failures; `--check-update` checks only. Internal workers skip this check.
Use `-a`/`--auto` for automatic execution; `-u` means update.

## Build and plan

```bash
meow run "Add CSV export" --name csv-export
meow plan "Add CSV export" --name csv-export
```

Worktrees are the default; `--no-worktree` uses the selected directory and makes
the name optional. `--from BRANCH` (`-b`) selects the starting branch.
Fresh runs get unique names; continuation reuses the saved worktree.
`--plan PATH` supplies a plan; `--resume-at review` reviews existing work first.
`--manually-approve-plan` (`-m`) pauses before implementation; plan-only rejects it.
`--auto --background` detaches an automatic run. Auto rejects in-place execution,
manual approval, resume-at-review, and lint-fix.

First use onboards the project. Required checks and review precede linked-worktree
commit/push; in-place runs do not deliver automatically. For clean-tree rules,
Jira flag restrictions, lint-fix, checkpoints, cancellation, and recovery,
see [RUNS.md](RUNS.md).

## Multi-sprint runs

```bash
meow run --orchestrate "Add SSO and audit logs" --max-parallel 3
meow run --jira PROJ-1 PROJ-2
meow run --jql "project = PROJ"
meow run --tasks tasks.toml --dry-run
meow resume ID --continue [--task T --request "Updated request"]
```

`--orchestrate` (`-o`) coordinates complete sprints for independent deliverables.
Task files, JQL, and multiple Jira keys select it automatically; one key does not.
Choose one objective, `--jira`, `--jql`, or `--tasks`. A clean checkout is required.
Only final objective review authorizes push. Exit code 3 means the branch was
delivered but `PR not opened`; output includes a manual link.
See [ORCHESTRATION.md](ORCHESTRATION.md) for task formats, recovery, and exit codes.
`/meow:run` chooses the engine; native supervision requires explicit opt-in.
Lifecycle commands route by ID; resume needs `--continue` to execute.
Multi-sprint mode accepts `--auto`, `--from`, `--test`, and `--no-pr`, but rejects
sprint-only name/worktree/plan/workflow flags, manual approval, and `--background`.

## Review

Select one source: prompt, `--jira [KEY]`, `--gitlab URL`, `--github URL`,
`--branch BRANCH --target TARGET`, `--plan PATH`, `--review-file PATH`, or `--ci`.
No source selects the latest plan, then the diff/whole project if none exists.

Reviews are report-only unless `--fix` is given; remote PR/MR and CI sources
reject fixes. Fix loops stop at `max_rounds`. Branch reviews use a worktree;
`--no-worktree` requires the branch already checked out. Other sources review
the selected checkout and permit pending changes.

`--scope project` covers every inventoried area, including frontend/backend,
despite pending changes or an existing plan. Prompt/Jira reviews also accept
`diff` or `auto`; whole-project wording such as "from 0" selects project scope.
Project verdicts require grounded, current `COVERAGE:` records.
Local PASS also requires current configured lint/test/build evidence;
remote reviews use their supplied diff without local checks.

`--review-file PATH` (`-r`) always resumes fixes for prompt/plan reviews.
Pass the original prompt for prompt reviews; rerun remote/branch sources instead.
A new focus requires fresh review; project wording widens an earlier diff scope.
Explicit `--plan PATH --test` enables tester mode; other review sources reject it.
Reports use helper-generated paths: plan reviews are `<plan>-review.md`;
others are `<flavor>.<8-hex-id>.review.md`. Legacy fixed names still resume.

### Scheduling a run

```bash
meow schedule add --at "2026-12-01T09:00:00+02:00" -- run "Add CSV export" --name csv-export
meow schedule list --work-dir /project
meow schedule edit ID --at "10:00" --work-dir /project
meow schedule cancel ID --work-dir /project
```

ISO timestamps must be future; `HH:MM` means the next machine-local occurrence.
Windows Task Scheduler requires a logged-in user; Linux requires a systemd user
manager (lingering for logged-out execution). Missed triggers may run when available.
Keep the registered project/runtime paths and scheduler credentials available.

Only `run` may follow `--`; scheduling adds `--auto` and rejects manual approval,
`--no-worktree`, lint-fix, background, and source-branch selection.
State and logs live in `.meow/schedules/`. Editing preserves the frozen recipe
and ID; only pending schedules are editable, with rollback on OS-update failure.
Cancellation removes the trigger; stop an already-started run via run/session controls.
`/meow:schedule` and UI scheduling use the same helper; see [UI.md](UI.md#scheduling).

## Definitions, hooks, and checks

Select workflows with `--workflow NAME`; repeated `--input name=value` supplies
strings (last value wins). Default selection, scopes, schemas, catalog commands,
and resume rules are in [WORKFLOWS.md](WORKFLOWS.md).

`meow hooks status claude` reads `active`/`missing`/`modified` state.
`install claude --only NAME` installs accepted hooks; repeat `--only`.
`uninstall claude` removes only owned entries. Hooks are optional, including in auto.
Names: `lint`, `shaping`, `plan_capture`, `plan_stop`.
Lint/shaping PostToolUse findings use `additionalContext`; plan Stop advisories
use `systemMessage`. Capture runs after plan Write/Edit only without a sidecar;
Stop checks exactly one open plan. Reinstall migrates old event bindings.

Quality concerns cite paths, evidence, impact, and follow-up; they are advisory,
deduplicated per run, and visible in verbose status. There is no numerical score.
Browser checks and server lifecycle are [configured separately](INTEGRATIONS.md#browser-tester-providers).

## Native and CLI skill modes

`/meow:run` defaults to CLI; plan/review/lint default to native.
Orchestrator task sprints remain detached CLI workers. Native integrations use
session-connected MCP tools; CLI uses configured launchers.
Named workflow trees execute through CLI. Onboard/migration/customize configure
the project rather than provide execution engines.

Native `review-checks --background` starts durable verification; `--status`
returns evidence/log paths. `native verdict` enforces checks and scope coverage.
`prompt`, `custom`, and `run review` accept `--host codex` (or actual
host) and repeated `--available-model ID`. Unsupported models return null to
inherit the host model, with fallback metadata. See the [native protocol](../skills/_shared/native-mode.md).

## CI review

`meow review --ci` reviews a detached checkout matching `CI_COMMIT_SHA` against
`refs/remotes/origin/<target>` (`[delivery].target_branch`, default `dev`).
Review uses local Git history without MCP and defaults to report-only.
Use [the template](../templates/gitlab-ci-review.yml) for detached MR pipelines and
non-target branch pushes. Fetch full history and the target without moving HEAD.
Merged-result/tag/other pipelines, `--fix`, `--test`, and `--no-worktree` are rejected.
MR descriptions supply the brief; pushes review the diff. Supply masked SDK credentials.
`--target-ref REF`, `--artifact-dir PATH`, and an existing `--plan PATH` are supported.
Upload `.meow/ci-artifacts/review.md` and `verdict.json` even on failure.
Exit: 0 PASS, 1 FAIL, 2 infrastructure/unverified.

Add `--post-comments gitlab` with masked `MEOW_GITLAB_TOKEN` (`api` scope and comment
access), or `--post-comments github` with `MEOW_GITHUB_TOKEN` (Pull requests write). See
[the integration skill](../skills/integrate-ci-review/SKILL.md) for CI variables.
Posting mode requires a PR/MR event and validates added/deleted-line locations
and remote revisions. Failures exit 2 with artifacts. Notes use the token's
identity; reruns may duplicate notes and API failures can leave partial results.

## Maintainer and internal commands

- `meow docs-update`: prose updates on a clean delivery target branch. First use
  `--since REF`, then commit docs and `docs/.meow-docs-update.json` together;
  later runs use that baseline. It reports changes without committing/pushing.
- `meow native`: deterministic JSON helpers for skill authors/debugging.
- `meow evaluate`: read-only harness evaluation over saved runs.
- `meow _worker`: private implementation detail for automatic background runs.
- `meow _orchestrator-task`: private detached task worker.
- `meow _scheduled ID`: private at-most-once schedule worker.

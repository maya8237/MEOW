# CLI guide

Use `meow COMMAND --help` for flags. Guides: [runs](RUNS.md), [orchestration](ORCHESTRATION.md),
[UI](UI.md), [configuration](INTEGRATIONS.md), [workflows](WORKFLOWS.md).

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
| `meow cleanup [ID]` | [Keep results and remove integrated task artifacts](CLEANUP.md) |
| `meow detach [--log FILE] -- CMD...` | Detach a command; returns PID and log JSON |
| `meow queue ["request"]` | Add a request, or process pending requests serially |
| `meow workflows` / `meow agents` | Inspect, eject, validate, or diff definitions |
| `meow skills sync [--scope S] [--check]` | Register custom skills with Claude Code; `--check` only reports |
| `meow ui` | Read-only catalog, session history, and docs |
| `meow schedule` | Add, list, edit, or cancel a one-shot trigger |
| `meow onboard` | [Configure a project without prompts](ONBOARDING.md); optional integration/feature flags |
| `meow ipython` | Interactive command session |

`mw` aliases `meow`. Shortcuts: `r` run, `p` plan,
`rv` review, `l` lint (`run --lint-fix`), `s` status, `x` cancel,
`c` resume, `q` queue, `w` worktree, `i` ipython.

Bare `meow` opens IPython. Use `meow run` to preserve IPython's `%run`;
other commands work directly or through `%meow`/`!meow`.

## Common options

All public commands accept `--work-dir PATH` (`-d`); native helpers also accept
`--working-dir`. Artifacts default to `.meow/plans/` unless `docs_dir` is set.

`meow --update` (`-u`) installs the latest published stable release, never
a branch tip; it cannot accompany a project command. It installs that version's
checksum-verified wheel from the package index into a venv built by the newest
Python 3.12+ found. The installer registers the plugin before switching
launchers in `~/.meow/bin`; config, caches, and earlier runtimes are retained.
Failures return nonzero. Keep runtimes used by workers/schedules. Rerun the installer
for setup prompts or editable-install migration; remove obsolete launchers shadowing PATH.
After installing, it lists the onboarding options added since the previous
version, read from the new release, and suggests `/meow:onboard` in each project.
It checks no project, so options already configured are still listed. Listing is
best-effort and skipped for checkout installs. In a terminal it then offers Bun's
shared dependency store once and migrates registered repos in the background.

Public commands warn on stderr about newer versions, silently ignoring network
failures; `--check-update` checks only. Internal workers skip this check.
Runs execute automatically; `-m` adds a plan approval pause. `-u` means update.

## Build and plan

```bash
meow run "Add a dark mode toggle" --name dark-mode
meow plan "Add a dark mode toggle" --name dark-mode
```

Normal runs use a worktree and commit/push after verification.
`-n` / `--no-worktree` uses the selected checkout without committing or pushing.
`-nc` / `--no-worktree-commit` uses it and commits without pushing;
`-np` / `--no-worktree-push` uses it and commits/pushes. These options are exclusive.
An existing linked worktree selected with `-d PATH` is used directly.
Omit `--name` to derive a short request name. `--from BRANCH` (`-b`) selects the starting branch.
`--from` chooses the start in a new worktree. Existing linked worktrees are reused without `--from`. `--target` overrides `[delivery].target_branch` (auto-onboarded to existing `dev`, otherwise `master`). Targets survive resume; [MR/pipeline options](PIPELINE.md).
`--plan PATH` supplies a plan; `--resume-at review` reviews existing work first.
`--manually-approve-plan` (`-m`) pauses before implementation; plan-only rejects it.
`--background` detaches a run and rejects manual approval and lint-fix.

First use onboards the project. Checks and review precede commits and pushes.
See [runs and recovery](RUNS.md) and [test selection / `--force-disable-tests`](VERIFICATION.md).

## Multi-sprint runs

```bash
meow run --orchestrate "Add SSO and audit logs" --max-parallel 3
meow run --jira PROJ-1 PROJ-2
meow run --jql "project = PROJ"
meow run --tasks tasks.toml --dry-run
meow resume ID --continue
meow resume ORCH_ID --continue --task T --request "Updated request"
```

Task files, JQL, and multiple Jira keys select orchestration automatically.
`--orchestrate` (`-o`) selects it explicitly; `--max-parallel N` limits concurrent
sprints, `--dry-run` saves the decomposition, and `--no-mr` skips opening an MR.
Local edits require explicit `--from BRANCH`; execution uses an isolated worktree.
Exit code 3 means the branch was delivered but `MR not opened`; output includes a manual link.
See [ORCHESTRATION.md](ORCHESTRATION.md) for sources, flags, delivery gates, and recovery.
`/meow:run` chooses the engine; native supervision requires explicit opt-in.

## Review

Select one source: prompt, `--jira [KEY]`, `--gitlab URL`, `--github URL`,
`--branch BRANCH --target TARGET`, `--plan PATH`, `--review-file PATH`, or `--ci`.
No source selects the latest plan, then the diff/whole project if none exists.

Reviews are report-only unless `--fix` is given; remote MR and CI sources
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
others are `<flavor>.<8-hex-id>.review.md`.

### Scheduling a run

```bash
meow schedule add --at "2026-12-01T09:00:00+02:00" -- run "Add a dark mode toggle" --name dark-mode
meow schedule list --work-dir /project
meow schedule edit ID --at "10:00" --work-dir /project
meow schedule cancel ID --work-dir /project
```

ISO timestamps must be future; `HH:MM` means the next machine-local occurrence.
Windows Task Scheduler requires a logged-in user; Linux requires a systemd user
manager (lingering for logged-out execution). Missed triggers may run when available.
Keep the registered project/runtime paths and scheduler credentials available.

Only `run` may follow `--`; scheduling rejects manual approval, lint-fix,
background, and source-branch selection. In-place options retain their usual
commit/push behavior.
State and logs live in `.meow/schedules/`. Editing preserves the frozen recipe
and ID; only pending schedules are editable, with rollback on OS-update failure.
Cancellation removes the trigger; stop an already-started run via run/session controls.
`/meow:schedule` uses this helper.

## Definitions and checks

Select workflows with `--workflow NAME`; repeated `--input name=value` supplies
strings (last value wins). Default selection, scopes, schemas, catalog commands,
and resume rules are in [WORKFLOWS.md](WORKFLOWS.md).

Quality concerns cite paths, evidence, impact, and follow-up; they are advisory,
deduplicated per run, and visible in verbose status. There is no numerical score.
Browser checks and server lifecycle are [configured separately](INTEGRATIONS.md#browser-tester-providers).

## Native and CLI skill modes

`/meow:run` defaults to CLI; plan/review/lint default to native.
Orchestrator task sprints remain detached CLI workers. Native integrations use
session-connected MCP tools; CLI uses configured launchers.
Named workflow trees execute through CLI. Onboard/customize configure
the project rather than provide execution engines.

Native `review-checks --background` starts durable verification; `--status`
returns evidence/log paths. `native verdict` enforces checks and scope coverage.
`prompt`, `custom`, and `run review` accept `--host codex` (or actual
host) and repeated `--available-model ID`. Unsupported models return null to
inherit the host model, with fallback metadata. See the [native protocol](../skills/_shared/native-mode.md).

## CI review

`meow review --ci` reviews a detached checkout matching `CI_COMMIT_SHA` against
`refs/remotes/origin/<target>` (`[delivery].target_branch`, existing `dev` or `master`).
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
Posting mode requires an MR event and validates added/deleted-line locations
and remote revisions. Existing comments inform the review; matching inline notes
(reworded, or open with a moved line) are skipped on reruns. Open threads meow
started (marked `<!-- meow-review -->`) that the reviewer finds fixed are resolved
with a "Resolved: reason" reply, or "✅ Solved: reason" if resolving is refused.
Unreadable history exits 2. Publishing failures keep the PASS/FAIL exit code and
appear as `comments_error` in `verdict.json`; partial publishes can leave notes posted.

## Maintainer and internal commands

- `meow docs-update`: prose updates on a clean delivery target branch. First use
  `--since REF`, then commit docs and `docs/.meow-docs-update.json` together;
  later runs use that baseline. It reports changes without committing/pushing.
- `meow native`: deterministic JSON helpers for skill authors/debugging.
- `meow evaluate`: read-only harness evaluation over saved runs.
- `meow _worker`: private implementation detail for automatic background runs.
- `meow _orchestrator-task`, `meow _scheduled ID`: private task and schedule workers.

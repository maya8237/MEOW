---
name: run
description: Use when building features, resolving one or more Jira issues, or coordinating an objective or task file into one reviewed delivery. Defaults to CLI; native execution requires an explicit request.
---

# run

If the user asks to run at a future time, follow
[`../schedule/SKILL.md`](../schedule/SKILL.md) before either execution mode.
Register the scheduled automatic run now and report the returned schedule;
do not begin the sprint or hold this session open until the time arrives.
Dependency or install problems: [dependencies](../_shared/dependencies.md).

CLI mode is the default: `meow run` owns project understanding, planning,
implementation, verification, review, and checkpoints.

## Native mode

Native mode is an explicit opt-in. Read [the shared protocol](../_shared/native-mode.md),
then follow [the native run procedure](references/native.md) for tester routing,
checkpoints, resume checks, and finalization. Do not load these for CLI runs.
For explicit native supervision with `--orchestrate`, follow
[the multi-sprint procedure](references/orchestration.md) instead. Its child
sprints remain detached CLI workers.

## CLI mode

Choose the execution scope from the work, without asking the user to know the
engine names. Briefly state the choice before launching:

- Detect the scope automatically; never wait for wording like "in parallel".
  Split the request into deliverables (separate features, fixes, or issues).
- Use ordinary `meow run` for one fix or cohesive feature with one shared plan,
  or when deliverables are tightly coupled (same code, one needs the other's
  output). It already schedules independent plan tasks in parallel.
- Add `--orchestrate` (`-o`) when there are two or more deliverables that can
  be built without each other, or the user explicitly requests it. Each gets
  its own plan, review, and recovery, and independent ones run in parallel
  (`--max-parallel`); the decomposer adds `depends_on` for any that must
  follow another. Results merge into one reviewed branch and PR. Multiple
  files or a long request alone do not justify separate sprints.
- `--tasks FILE`, `--jql QUERY`, and multiple `--jira KEY...` select full
  orchestration automatically; no additional flag is needed. A single Jira
  issue stays an ordinary sprint unless `--orchestrate` is explicit.
- Honor a named `--workflow`, supplied `--plan`, manual plan approval, or
  `--no-worktree` using the ordinary sprint flow; these are incompatible with
  full orchestration. If the user explicitly requests incompatible modes,
  explain the conflict instead of silently dropping an option.
- Continue saved work with `meow resume ID --continue`, never a fresh run;
  the saved ID determines its engine.

```bash
meow run "Add SSO and a separately scoped audit-log feature" --orchestrate
meow run --jira PROJ-1 PROJ-2
meow run --tasks tasks.toml
```

`--from BRANCH` selects the starting branch; `--target BRANCH` selects the MR/PR
target. Recognize "start from release, merge into dev" as
`--from release --target dev`. Without `--target`, use `[delivery].target_branch`,
auto-onboarded to existing `dev`, otherwise `master`; never infer the target from
`--from` or the current checkout. Keep existing worktree reuse unchanged.
The target is printed and saved in the journal; resume the saved run without
choosing a new target. In-place flags do not open an MR. `--no-mr` disables
MR creation and pipeline verification.

Final delivery opens the MR/PR into the saved target and fixes
code until the pipeline's `meow-review` job passes. Tell the user the MEOW loop
(generate, review, checks) has finished and the feature is done, and that the
work is now in final delivery, being verified through the MR pipeline. Relay
the `[delivery]` lines as they appear. Never say the work is verified,
delivered and verified, or done-and-verified unless the output contains
`Verified by pipeline`; for `Not verified by pipeline`, report the reason and
the MR/pipeline URLs. Pass `--no-mr` only if the user does not want an MR, and
`--fix-non-meow-jobs` only if they explicitly ask to fix unrelated (non-MEOW)
jobs; deploy jobs are never fixed. Details: [pipeline](../../docs/PIPELINE.md).

Full orchestration uses isolated detached CLI sprints, pushes only after the
combined objective passes review, and opens one PR unless `--no-mr` is given.
It accepts `--from`, `--max-parallel`, `--dry-run`, and `--test`; `--dry-run`
saves the decomposition for later continuation. Do not pass `--name`,
`--background`, or sprint-only workflow/plan flags to this mode. Inspect with
`meow status ID`, continue with `meow resume ID --continue`, and stop with
`meow cancel ID`. To reset a blocked task, resume with `--task TASK` and, only
when the user supplied it, `--request "Complete replacement task request"`.
For multi-sprint delivery, report the printed orchestrator/branch/PR/task-count
JSON and report path. Exit 3 means the branch was pushed but PR creation failed;
include `MR not opened` and its manual link. For blocked/failed runs, report the
blocker, evidence, and continuation command; do not claim delivery.

Choose a workflow when the user names one: pass `--workflow <name>` to
`meow run`. Inspect available choices with `meow workflows list` and
`meow workflows show <name>`. Shipped choices are `default` (the full run),
`quick` (short auto-approved plan, checks before review; `-m` still pauses for approval),
and `review-and-fix` (reviews and fixes
existing code, with checks and no automatic delivery). Without a flag, honor
`[workflow] default` in config. Pass named inputs as repeated
`--input name=value` flags. Custom workflows execute through CLI mode;
the native protocol does not interpret their step trees.

When the user requests `--test`, or asks to run tests, test it, or validate
behavior, pass `--test` to
`meow run` (including Jira and `--resume-at review` flows); configured servers
stay alive throughout tester validation.
If the user asks to disable tests, pass `--force-disable-tests`: it overrides
`--test`, skips the AI tester and configured test/browser gates, and keeps lint/build.

For ordinary sprints, run from the project root (separate SDK sessions, headless):

```bash
meow run "<feature request>" --name "<generated-feature-name>" --background --work-dir "<project-path>"
meow run --jira [ISSUE-KEY] --work-dir "<project-path>"   # Jira-sourced, retained branch
```

**Launching and watching:** from a managing session, start ordinary runs with
`--background` so a Bash timeout or compaction cannot kill them (orchestration
rejects the flag and already detaches its sprints). After launching any
`meow run` (or `--orchestrate`), create a recurring status check with
`CronCreate` every 7 minutes (`*/7 * * * *`). Each tick: run
`meow status ID --json`. For `retryable: true` with `settled` (parked)
and `budget_left > 0`, run `next_command` exactly and report one line. For
`retryable: false`, report `cause`, `last_failure` and the evidence paths, then stop the
cron. Stop only on `complete`/`delivered`/`exhausted`/`cancelled` or `retryable: false`,
with no stop on `failed` alone. Report only meaningful changes (phase, task
count, blockers, delivery). Scheduled runs still register through
`/meow:schedule` and never sleep or poll in the session.

A command exiting `75` (or printing `MEOW_RETRY`) is transient, including bare
OSError-class failures: wait `retry_after` (it can be long for an API budget or rate
limit; the cron simply skips ticks until then), then rerun the printed `next_command`
or the same native helper, up to `managed_resumes` (default 6), and stop when
`meow status ID --json` shows no progress. Exit 1 / `MEOW_BLOCKED` (including
`stop=budget_exhausted`) means stop dependent work and report. Resume is attempted,
not guaranteed: when status gives a reason (blocking `cause`, `not_resumed_reason`,
budget exhausted, no progress, `not_resumable`, live driver), do not resume; report it.
On entry, check `meow status --json` and `meow resume --parked` for existing
parked runs before starting new work.

**Resuming in CLI mode:** if the user asks to continue, resume, pick up, or
build on existing code, a worktree, or an earlier plan (any wording), do not
start a fresh run -- a plain `meow run --name X` plans from scratch in its own
new worktree. When a saved run ID is available, inspect it with
`meow status <run_id> --json --work-dir "<project-path>"`. Continue saved
runs (only when status gives no stop reason) with `meow resume <run_id> --continue --work-dir
"<project-path>"`. If there is no saved run, look in
`<project-path>/.worktrees/<name>/<docs_dir>` for a plan
(`meow native latest-plan --work-dir "<project-path>" --active-dir
"<project-path>/.worktrees/<name>"`). If one exists with a numbered task list
and a `## Sprint Contract`, run `meow run "<request>" --name "<name>"
--resume-at review --plan "<plan_file>" --work-dir "<project-path>"`,
which reviews the existing code and generates only what is missing. If there
is none, or it is incomplete, tell the user and run normally.

Add `-n` / `--no-worktree` to operate in the selected checkout (name optional; not
applicable to `--jira`, which always uses its own pushable-branch worktree).
Runs execute automatically and commit/push verified work from their worktree.
`-n` leaves changes uncommitted; `-nc` / `--no-worktree-commit` operates in the
selected checkout and commits only; `-np` / `--no-worktree-push` operates there
and commits/pushes. Existing linked worktrees selected with `--work-dir` are
used directly. Add `-m` for plan approval (also when the user asks to review or approve the plan first);
`--background` rejects manual approval and lint-fix. `meow -u` updates MEOW.
Successful runs automatically retain the result and clean integrated task artifacts.
Use `meow cleanup [RUN_ID]` to retry cleanup; omitted IDs scan saved runs.
Report retained artifacts and their reasons; never force-delete unfinished work.
Stream its progress (`[planner]`, `[generator]`,
`[reviewer]`) to the user. Report the plan and review file paths on success
(or, for `--jira`, the printed `{"issue": ..., "branch": ...}` JSON line), or
that it failed after `max_rounds` and where the last review is.

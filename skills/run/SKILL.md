---
name: run
description: Use when building features, resolving one or more Jira issues, or coordinating an objective or task file into one reviewed delivery. Defaults to CLI; native execution requires an explicit request.
---

# run

If the user asks to run at a future time, follow
[`../schedule/SKILL.md`](../schedule/SKILL.md) before either execution mode.
Register the scheduled automatic run now and report the returned schedule;
do not begin the sprint or hold this session open until the time arrives.

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

- Use ordinary `meow run` for a fix or cohesive feature with one shared plan.
  It already schedules independent implementation tasks in parallel.
- Add `--orchestrate` (`-o`) for an objective containing independently scoped
  deliverables that need their own plans, reviews, and recovery, or when the
  user explicitly requests full sprint orchestration. It combines the sprints
  into one reviewed branch and PR. Multiple files or a long request alone do
  not justify separate sprints.
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

Full orchestration uses isolated detached CLI sprints, pushes only after the
combined objective passes review, and opens one PR unless `--no-pr` is given.
It accepts `--from`, `--max-parallel`, `--dry-run`, and `--test`; `--dry-run`
saves the decomposition for later continuation. Do not pass `--name`,
`--background`, or sprint-only workflow/plan flags to this mode. Inspect with
`meow status ID`, continue with `meow resume ID --continue`, and stop with
`meow cancel ID`. To reset a blocked task, resume with `--task TASK` and, only
when the user supplied it, `--request "Complete replacement task request"`.
For multi-sprint delivery, report the printed orchestrator/branch/PR/task-count
JSON and report path. Exit 3 means the branch was pushed but PR creation failed;
include `PR not opened` and its manual link. For blocked/failed runs, report the
blocker, evidence, and continuation command; do not claim delivery.

Choose a workflow when the user names one: pass `--workflow <name>` to
`meow run`. Inspect available choices with `meow workflows list` and
`meow workflows show <name>`. Shipped choices are `default` (the full run),
`quick` (omits knowledge and shaping), and `review-only` (reviews and fixes
existing code, with checks and no automatic delivery). Without a flag, honor
`[workflow] default` in config. Pass named inputs as repeated
`--input name=value` flags. Custom workflows execute through CLI mode;
the native protocol does not interpret their step trees.

When the user requests `--test`, pass it through to
`meow run` (including Jira and `--resume-at review` flows); configured servers
stay alive throughout tester validation.

For ordinary sprints, run from the project root (separate SDK sessions, headless):

```bash
meow run "<feature request>" --name "<generated-feature-name>" --work-dir "<project-path>"
meow run --jira [ISSUE-KEY] --work-dir "<project-path>"   # Jira-sourced, retained branch
```

**Watching a started run:** after launching any `meow run` (or `--orchestrate`)
as a command from this session, create a recurring status check with
`CronCreate` every 7 minutes (`*/7 * * * *`). Each tick runs
`meow status <run_id> --work-dir "<project-path>"`, reports only meaningful
changes (phase, task count, blockers, delivery), and surfaces failures. Stop
the check with `CronDelete` once the run reaches a terminal state (delivered,
failed, blocked, or cancelled) or the user cancels it. This is separate from
scheduled runs, which still register through `/meow:schedule` and never
sleep or poll in the session.

**Resuming in CLI mode:** if the user asks to continue, resume, pick up, or
build on existing code, a worktree, or an earlier plan (any wording), do not
start a fresh run -- a plain `meow run --name X` plans from scratch in its own
new worktree. When a saved run ID is available, inspect it with
`meow status <run_id> --work-dir "<project-path>"`. For a
`needs_user_decision` pause before planning, obtain the intended outcome and
continue the same run with `meow resume <run_id> --continue --request
"<complete clarified request>" --work-dir "<project-path>"`; include all
remaining requirements because this replaces the whole request. Other saved
runs continue with `meow resume <run_id> --continue --work-dir
"<project-path>"`. If there is no saved run, look in
`<project-path>/.worktrees/<name>/<docs_dir>` for a plan
(`meow native latest-plan --work-dir "<project-path>" --active-dir
"<project-path>/.worktrees/<name>"`). If one exists with a numbered task list
and a `## Sprint Contract`, run `meow run "<request>" --name "<name>"
--resume-at review --plan "<plan_file>" --work-dir "<project-path>"`,
which reviews the existing code and generates only what is missing. If there
is none, or it is incomplete, tell the user and run normally.

Add `--no-worktree` to operate in the main repo (name then optional; not
applicable to `--jira`, which always uses its own pushable-branch worktree).
Use `-a` / `--auto` for non-interactive delivery or `--background`
(`meow -u` updates MEOW); it requires
an isolated worktree and cannot be combined with `--no-worktree`, manual plan
approval, `--resume-at review`, or `--lint-fix`. An in-place
`--no-worktree` run does not automatically commit or push.
Successful runs automatically retain the result and clean integrated task artifacts.
Use `meow cleanup [RUN_ID]` to retry cleanup; omitted IDs scan saved runs.
Report retained artifacts and their reasons; never force-delete unfinished work.
Stream its progress (`[planner]`, `[generator]`,
`[reviewer]`) to the user. Report the plan and review file paths on success
(or, for `--jira`, the printed `{"issue": ..., "branch": ...}` JSON line), or
that it failed after `max_rounds` and where the last review is.

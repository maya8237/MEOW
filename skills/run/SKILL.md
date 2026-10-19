---
name: run
description: Use when building a feature or resolving a Jira issue through a full MEOW sprint. Defaults to CLI; native execution requires an explicit request.
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

## CLI mode

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

Runs `meow run` (separate Agent SDK sessions, works headless). From the project root:

```bash
meow run "<feature request>" --name "<generated-feature-name>" --work-dir "<project-path>"
meow run --jira [ISSUE-KEY] --work-dir "<project-path>"   # Jira-sourced, retained branch
```

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
Stream its progress (`[planner]`, `[generator]`,
`[reviewer]`) to the user. Report the plan and review file paths on success
(or, for `--jira`, the printed `{"issue": ..., "branch": ...}` JSON line), or
that it failed after `max_rounds` and where the last review is.

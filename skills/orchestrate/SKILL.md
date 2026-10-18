---
name: orchestrate
description: Split an objective, a set of Jira issues, a JQL query, or a task file into a graph of tasks, run each one as its own automatic MEOW sprint in parallel, merge every passing task into one integration branch, review the whole result against the objective, and open a single pull request -- or stop with nothing pushed and a precise report. Runs natively in this Claude Code session by default, where you supervise; CLI mode runs the same engine headless.
---

# orchestrate

First follow [the shared update check](../_shared/update-check.md).

One objective in, one reviewed branch and one pull request out. The orchestrator
decomposes the work into tasks, runs every task as an ordinary automatic
sprint in its own worktree (detached CLI child processes, up to `max_parallel`
at a time), merges each passing task into the integration branch
`orchestrator/<id>`, recovers failed tasks through triage (at most three cycles
per task), reviews the combined branch against the objective, pushes it, and
opens a pull or merge request. If a task cannot be recovered or the final review
keeps failing, it stops with **nothing pushed** and says exactly what blocks it.

Native mode is the default: this session is the supervisor. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) first; its "The orchestrate helpers" section defines
every `meow native orchestrate` command and its JSON. Use **CLI mode** (bottom)
when the user asks for the CLI, a headless or scheduled run, or `--dry-run`.

Task sprints never run inside this session or in subagents: they are the same
detached CLI children in both modes, so they keep going if the session ends.

## Native mode

Pass `--work-dir "<project-path>"` to every command (omitted below for
brevity). Each prints one JSON document; on a nonzero exit report the stderr
message verbatim and stop. An error saying the orchestrator is "driven by a live
... driver" means another session or a terminal owns it: tell the user instead
of fighting for it.

1. **Pick the source** (exactly one):
   - an **objective**: whatever the user typed;
   - a **tasks file** the user named (TOML or JSON, format in docs/CLI.md);
   - **Jira keys** or a **JQL query**: fetch through the Jira MCP tools already
     connected to this session (tool names containing `jira`; read-only, never
     comment or transition). For JQL, run the search and read every match. Each
     issue needs `key`, `summary`, and `description`; stop and report if one is
     missing or nothing matched, and stop if there are more than 32 issues (ask
     the user to narrow the query). Write
     `<project-path>/.meow/orchestrator-input.json` as
     `{"tasks": [{"id": "<KEY>", "request": "Resolve Jira issue <KEY>: <summary>\n\n<description>", "source_ref": "<KEY>"}]}`
     with one entry per distinct key and the key itself as the id.
   If nothing was given, ask for a one-line objective.
2. **Start it.** Run `meow native orchestrate init --objective "<text>"` (or
   `--tasks <file>`), adding `--from <branch>` (base branch, default the current
   one), `--max-parallel N`, `--test` (each task also runs the configured
   tests), or `--no-pr` when the user asked for them. The checkout must have no
   uncommitted changes. Keep `id` and `integration_worktree` from the JSON.
3. **Decompose** (you are the decomposer). If `decomposer` is `null` the tasks
   are saved: go to step 4. Otherwise follow its `system_prompt` and `query`
   yourself: read the repository with read-only tools, then write the JSON that
   prompt describes to its `output_file` (the only file you write). For a Jira
   set keep every issue as its own task and only add `depends_on` and
   `expected_paths`. Run `meow native orchestrate tasks <id> --file <output_file>`.
   If `ok` is false, fix exactly what `error` says and run it once more
   (`retry_allowed`); a second rejection fails the orchestrator, so say so and
   stop.
4. **Supervise.** Repeat `meow native orchestrate tick <id>` and act on `state`:
   - `waiting`: nothing needs you. Run `meow native orchestrate wait <id>` as a
     **background command** (Bash with `run_in_background`) and tick again when
     it finishes. It returns as soon as any running child changes, or after 300
     seconds. Never poll with `sleep`.
   - `conflicts`: a passing task could not merge cleanly. For each entry, brief
     a **fresh subagent** (Agent tool, `general-purpose`) with the entry's
     `prompt.system_prompt`, a blank line, and `prompt.query`. Tell it the
     working directory is `integration_worktree`, that it may use only Read,
     Grep, Glob, Edit, and Write, may edit only the listed `paths`, and must not
     run git or touch any other file. Then run
     `meow native orchestrate merge-done <id> --task <task>`: `merged` or
     `noop` means keep ticking; `conflict` means markers remain in the returned
     `paths` (re-brief once, otherwise abort); `checks_failed` means the merge
     was rolled back and the task comes back under `failures`. To give up on a
     merge run `meow native orchestrate merge-abort <id> --task <task>`; the
     task goes to triage as an integration failure.
   - `failures`: an attempt failed, and you are the triage role. For each entry
     read every file in `evidence`, follow its `prompt.system_prompt` and
     `query`, and write the decision JSON to `prompt.output_file`. Triage is
     read-only: change no code, branch, or worktree. Then run
     `meow native orchestrate triage <id> --task <task> --file <output_file>`.
     A task `status` of `pending` is relaunched by the next tick; `blocked`
     means stop (see "When it blocks").
   - `all_merged`: go to step 5.
   - `blocked`: go to "When it blocks".
   - `cancelled`: say so and stop.
   After every tick give the user one short line: launched, merged, running,
   and any failures.
5. **Review the whole objective.** Run `meow native orchestrate review <id>`.
   On hosts other than Claude, pass `--host <host>` and repeat
   `--available-model <id>` for each advertised model, following the shared
   protocol's model-resolution rules. It
   runs the required checks in the integration worktree and returns
   `system_prompt`, `query`, `review_file`, `model`, `tools`, `round`,
   `max_rounds`, `checks_passed`, `check_failures`, and model-resolution
   metadata. Surface `model_fallback_reason` when supplied. Launch a **fresh
   subagent every round** (never yourself, never a reused one) with the
   `system_prompt`, a blank line, and the `query`; use `model` when it is not
   null; tell it the working directory is `integration_worktree`, that it must
   write its verdict to `review_file`, and to use only `tools`. Then run
   `meow native orchestrate review-result <id> --file <review_file>`:
   - `phase` is `delivering`: the review passed; go to step 6.
   - `phase` is `reviewing` (FAIL with `rounds_left` above 0): you are the
     review fixer. Read the findings in the review file, verify each against the
     code, and make the smallest in-scope fixes in `integration_worktree`
     (`meow native prompt review-fixer --active-dir "<integration_worktree>"`
     gives the fixer instructions; follow the shared protocol's lint discipline
     there). Do not commit; run `review` again for the next round, which commits
     your fixes first.
   - `phase` is `blocked`: the rounds are used up; go to "When it blocks".
   A failing required check always forces FAIL, even when the reviewer passes.
   `review-result` accepts only the report path returned by `review`. If code
   or configuration changed after that round's checks, run `review` again.
   Delivery also requires the same passing report and unchanged reviewed code.
   Source changes must be committed before the review that authorizes delivery.
6. **Deliver.** Run `meow native orchestrate deliver <id>`; it pushes the
   integration branch (a push failure fails the orchestrator and `resume`
   retries it). If `open_pr` is true, open the pull request (GitHub) or merge
   request (GitLab) with the GitHub or GitLab MCP tools already connected to
   this session: head `branch`, base `base`, the given `title` and `body`, not a
   draft, reusing an open one for that head if it exists. Then run
   `meow native orchestrate pr <id> --url <url>`. If it cannot be opened (no
   connected tool, an error) or `open_pr` is false, run
   `meow native orchestrate pr <id> --error "<reason or skip_reason>"`. The
   orchestrator is now `delivered`. Report one line,
   `{"orchestrator": "<id>", "branch": "<branch>", "pr": <url or null>, "tasks": <N>}`;
   when no PR was opened and it was not disabled, add `PR not opened: <reason>`
   and the `manual_link`. Point at the report path from the `pr` result.

### When it blocks

`blocked` (a task ran out of recovery cycles, triage needs a human, the final
review kept failing) and `failed` stop the run with nothing pushed. Run
`meow native orchestrate status <id>` and read `blocker` (`task_id`, `cause`,
`evidence`, `needed`) and `error`. Tell the user in plain words which task or
step, the cause, the evidence file paths, and what is needed. Then run
`meow native orchestrate release <id>` and give both resume commands,
`/meow:orchestrate resume <id>` and `meow orchestrate resume <id>`. To change a
blocked task's request, resume with `--task <task> --request "<new text>"`.
Worktrees and branches stay until `meow worktree clean <id>`.

### Resuming and cancelling

`/meow:orchestrate resume <id>` (a new session, or after a block): run
`meow native orchestrate resume <id>` (add `--task` and `--request` only when the
user gave them), then continue from `phase` in its JSON: `decomposing` is step 3
with the returned `decomposer`, `running` is step 4, `reviewing` is step 5,
`delivering` is step 6. It first rolls back a half-finished merge. Children that
kept running meanwhile are re-attached by the next tick. Find an id with
`meow native orchestrate status` (the latest orchestrator). To stop everything
run `meow native orchestrate cancel <id>`: running children are cancelled and
nothing is pushed.

### Rules

- Never run a task sprint yourself or in a subagent, and never edit a task
  worktree. Your only edits are the decomposer file, triage decision files,
  conflict resolutions (made by the subagent), and review fixes in the
  integration worktree.
- Never push, merge, or reset with git yourself; the helpers do it.
- Never decide from memory what comes next: the JSON from `tick`,
  `review-result`, and `status` is the source of truth.
- `--test` applies to each task's sprint. The native objective review runs the
  reviewer and the required checks; use CLI mode when a final tester run is
  needed.

## CLI mode

Runs the same engine with Agent SDK roles instead of this session (works
headless). From the project root:

```bash
meow orchestrate "<objective>" --work-dir "<project-path>"
meow orchestrate --jira PROJ-1 PROJ-2 --work-dir "<project-path>"
meow orchestrate --jql "project = PROJ AND sprint in openSprints()" --work-dir "<project-path>"
meow orchestrate --tasks tasks.toml --work-dir "<project-path>"
meow orchestrate "<objective>" --dry-run --work-dir "<project-path>"   # stop after decomposition
meow orchestrate status [ID] --work-dir "<project-path>"
meow orchestrate resume ID [--task T --request "<text>"] --work-dir "<project-path>"
meow orchestrate cancel ID --work-dir "<project-path>"
```

Start options: `--from BRANCH`, `--max-parallel N`, `--test`, `--no-pr`. Give
exactly one of an objective, `--jira`, `--jql`, or `--tasks`; `--jira` and
`--jql` need `[jira]` and `[jira.mcp]` in `.meow/config.toml` and are read-only.
The checkout must be clean. Exit codes: 0 delivered with a PR (or `--no-pr`), 3
delivered without a PR (the output says `PR not opened: <reason>` with a manual
link), 1 failed, 2 blocked, 130 cancelled. On delivery it prints
`{"orchestrator": ID, "branch": ..., "pr": URL-or-null, "tasks": N}`. Stream its
progress to the user and report that JSON line or, for a blocked or failed run,
the printed report (blocker first) and the resume command. Each task's sprint
also shows in `meow status` (source `orchestrator`). After delivery, clean up
with `meow worktree clean <ID>`.

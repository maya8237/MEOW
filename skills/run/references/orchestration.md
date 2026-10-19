# Native supervision of multiple sprints

## Native mode

Pass `--work-dir "<project-path>"` to every command (omitted below for
brevity). Each prints one JSON document; on a nonzero exit report the stderr
message verbatim and stop. An error saying the orchestrator is "driven by a live
... driver" means another session or a terminal owns it: tell the user instead
of fighting for it.

1. **Pick the source** (exactly one):
   - an **objective**: whatever the user typed;
   - a **tasks file** the user named ([TOML/JSON format](../../../docs/ORCHESTRATION.md#task-file));
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
2. **Start it.** Run `meow native run init --objective "<text>"` (or
   `--tasks <file>`), adding `--from <branch>` (base branch, default the current
   one), `--max-parallel N`, `--test` (each task also runs the configured
   tests), or `--no-pr` when the user asked for them. The checkout must have no
   uncommitted changes. Keep `id` and `integration_worktree` from the JSON.
3. **Decompose** (you are the decomposer). If `decomposer` is `null` the tasks
   are saved: go to step 4. Otherwise follow its `system_prompt` and `query`
   yourself: read the repository with read-only tools, then write the JSON that
   prompt describes to its `output_file` (the only file you write). For a Jira
   set keep every issue as its own task and only add `depends_on` and
   `expected_paths`. Run `meow native run tasks <id> --file <output_file>`.
   If `ok` is false, fix exactly what `error` says and run it once more
   (`retry_allowed`); a second rejection fails the orchestrator, so say so and
   stop.
4. **Supervise.** Repeat `meow native run tick <id>` and act on `state`:
   - `waiting`: nothing needs you. Run `meow native run wait <id>` as a
     **background command** (Bash with `run_in_background`) and tick again when
     it finishes. It returns as soon as any running child changes, or after 300
     seconds. Never poll with `sleep`.
   - `conflicts`: a passing task could not merge cleanly. For each entry, brief
     a **fresh subagent** (Agent tool, `general-purpose`) with the entry's
     `prompt.system_prompt`, a blank line, and `prompt.query`. Tell it the
     working directory is `integration_worktree`, that it may use only Read,
     Grep, Glob, Edit, and Write, may edit only the listed `paths`, and must not
     run git or touch any other file. Then run
     `meow native run merge-done <id> --task <task>`: `merged` or
     `noop` means keep ticking; `conflict` means markers remain in the returned
     `paths` (re-brief once, otherwise abort); `checks_failed` means the merge
     was rolled back and the task comes back under `failures`. To give up on a
     merge run `meow native run merge-abort <id> --task <task>`; the
     task goes to triage as an integration failure.
   - `failures`: an attempt failed, and you are the triage role. For each entry
     read every file in `evidence`, follow its `prompt.system_prompt` and
     `query`, and write the decision JSON to `prompt.output_file`. Triage is
     read-only: change no code, branch, or worktree. Then run
     `meow native run triage <id> --task <task> --file <output_file>`.
     A task `status` of `pending` is relaunched by the next tick; `blocked`
     means stop (see "When it blocks").
   - `all_merged`: go to step 5.
   - `blocked`: go to "When it blocks".
   - `cancelled`: say so and stop.
   After every tick give the user one short line: launched, merged, running,
   and any failures.
5. **Review the whole objective.** Run `meow native run review <id>`.
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
   `meow native run review-result <id> --file <review_file>`:
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
6. **Deliver.** Run `meow native run deliver <id>`; it pushes the
   integration branch (a push failure fails the orchestrator and `resume`
   retries it). If `open_pr` is true, open the pull request (GitHub) or merge
   request (GitLab) with the GitHub or GitLab MCP tools already connected to
   this session: head `branch`, base `base`, the given `title` and `body`, not a
   draft, reusing an open one for that head if it exists. Then run
   `meow native run pr <id> --url <url>`. If it cannot be opened (no
   connected tool, an error) or `open_pr` is false, run
   `meow native run pr <id> --error "<reason or skip_reason>"`. The
   orchestrator is now `delivered`. Report one line,
   `{"orchestrator": "<id>", "branch": "<branch>", "pr": <url or null>, "tasks": <N>}`;
   when no PR was opened and it was not disabled, add `PR not opened: <reason>`
   and the `manual_link`. Point at the report path from the `pr` result.

### When it blocks

`blocked` (a task ran out of recovery cycles, triage needs a human, the final
review kept failing) and `failed` stop the run with nothing pushed. Run
`meow native run status <id>` and read `blocker` (`task_id`, `cause`,
`evidence`, `needed`) and `error`. Tell the user in plain words which task or
step, the cause, the evidence file paths, and what is needed. Then run
`meow native run release <id>` and give the resume command,
`meow resume <id> --continue`. To change a
blocked task's request, resume with `--task <task> --request "<new text>"`.
Worktrees and branches stay until `meow worktree clean <id>`.

### Resuming and cancelling

`/meow:run resume <id>` (a new session, or after a block): run
`meow native run resume <id>` (add `--task` and `--request` only when the
user gave them), then continue from `phase` in its JSON: `decomposing` is step 3
with the returned `decomposer`, `running` is step 4, `reviewing` is step 5,
`delivering` is step 6. It first rolls back a half-finished merge. Children that
kept running meanwhile are re-attached by the next tick. Find an id with
`meow native run status` (the latest orchestrator). To stop everything
run `meow native run cancel <id>`: running children are cancelled and
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

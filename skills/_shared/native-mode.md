# Native mode: shared protocol

Other agent hosts can use the same helpers. In Codex, pass `--host codex` to
`meow native prompt` and `meow native custom`; repeat `--available-model ID` only for exact supported
IDs. Returned `model: null` means inherit the host model. `configured_model`
preserves the SDK setting; `model_fallback_reason` explains a fallback.
Default callers retain Claude behavior. Use the host's subagent API when its
tool names differ from Claude Code's Task tool.

Local reviews expose `meow native review-checks` (foreground),
`review-checks --background` (detached worker), and `review-checks --status`
(read-only evidence). Pass the same directory flags as `prompt`. Required
lint/test/build checks and project coverage are enforced by `native verdict`;
a model-authored PASS alone cannot finish a newly prepared local review.
Logs and atomic results live under `.meow/review-checks/`. Preserve the scope
and verification sidecars alongside their review report.

The workflow skills `plan`, `review`, `lint`, and `orchestrate` use **native
mode** by default when invoked inside Claude Code: *you* (this session) do the
planning, generation, fixes, or supervision, and you dispatch reviewer/explorer
work to Task subagents. No separate Agent SDK process is started. (The
`orchestrate` skill still runs every task sprint as a detached CLI child
process; only its judgement roles are native.) The `run` skill uses CLI mode by
default and opts into this protocol only when the user asks for native
execution. **CLI mode** is the `meow <command>` path documented in each
workflow skill. The `onboard`, `migration`, and `customize` skills are
guidance/configuration flows, not alternate execution engines.

Future-time requests use `/meow:schedule` in either mode. Register them with
`meow schedule add --at TIME -- run REQUEST --name NAME` before starting any
work. The OS trigger starts an unattended CLI run later; this session does not
wait or execute a native sprint immediately. Inspect, edit, and cancel through
`meow schedule list`, `meow schedule edit ID --at TIME`, and
`meow schedule cancel ID`. See
[`../schedule/SKILL.md`](../schedule/SKILL.md) for timezone and platform details.

Both modes read the same active MEOW configuration and write the same files, so a run
started in one can be continued in the other (`meow review --fix`,
`meow run --resume-at review`, `/meow:review`, ...).
Configuration priority is local project config, shared project config, then the
user fallback at `~/.meow/config.toml`.

Run checkpoints are stored in `.meow/runs/`. Inspect one with
`meow status [RUN_ID] [--verbose]` or `meow resume [RUN_ID]`. The latter only
inspects until `--continue` is supplied. Native execution
must write a checkpoint before and after planner, generator, reviewer, tester,
and required check phases; an interrupted generator turn is marked as a
possible mutation. Inspect and review the actual worktree against the saved
plan before another edit. Do not clean up failed or interrupted worktrees.
Completion requires current passing required lint, test, and build checks,
reviewer PASS, and enabled tester PASS. SDK usage missing from evidence is
`unavailable`.
Native reviewer PASS checkpoints require the actual current bound report and
save its content fingerprint; flags alone are not evidence. Finalization
revalidates that report and its binding. Native finalization always routes
configured tester projects to the CLI lifecycle, even if a caller supplied
`--tester PASS`.

## The `meow native` helper

`meow native <command>` prints one JSON document on stdout (failures: message
on stderr, exit 1). It starts no agents. If `meow` is not on PATH (in any
mode), tell the user to run the README's install script and stop; do not
guess at a venv path. Flags shared by every command:
`--working-dir PATH` (also `--work-dir`/`-d`; project root with
`.meow/config.toml`, default cwd) and, where applicable, `--active-dir PATH`
(the worktree that `prepare` returned; default = working dir).

| Command | Purpose |
|---|---|
| `workflow NAME` | Resolved workflow JSON with scope, fingerprint, and expanded steps; inspect custom workflows here and execute them through `meow run --workflow NAME` |
| `prepare [--name N] [--fresh] [--no-worktree] [--from B] [--branch BR] [--existing-branch BR] [--allow-dirty]` | Startup guards + worktree; returns `name` (the one used; `--fresh` may add `-2`, ...), `active_dir`, `use_worktree`, `docs_dir`, `plan_file`, `review_file`, `max_rounds`, `models`, `lint`, and onboarding facts |
| `verify [--no-lint]` | Validate config and report lint, tester, and integration readiness without starting agents, servers, or MCP connections |
| `latest-plan` / `latest-review` | Newest plan / review file in `docs_dir` (`latest-review` also gives its `flavor`: plan, prompt, gitlab, github, or branch) |
| `verdict FILE` | `{status: PASS\|FAIL, summary}` of a review file |
| `lint [--file F] [--fix] [--all-blocking]` | Per-file (auto-fixing) or project-wide lint run. `--all-blocking` ignores `gate` and treats every command as blocking (what the `lint` skill needs; everything else wants the default gate/informational split) |
| `round PLAN [--reset\|--show]` | On-disk round counter; default advances it |
| `checkpoint PHASE [--run-id ID] [--request TEXT] [--plan FILE] [--review FILE] [--round N] [--reviewer PASS\|FAIL] [--tester PASS\|FAIL]` | Atomic run journal transition; omit `--run-id` only to create a native run, then reuse the returned ID |
| `finalize RUN_ID` | Run current required lint, test, and build gates and finish only when reviewer and enabled tester evidence pass |
| `prompt ROLE [--plan F] [--focus T] [--target T --branch B] [--provider {gitlab,github}] [--worktree] [--shape F]` | Exact SDK system prompt, task message (reviewer roles include the harness lint evidence; `reviewer-mr` returns a `query_template` to fill instead), and model for a role |
| `push BRANCH` | Push a named branch to origin |
| `knowledge-audit` / `knowledge-check` / `knowledge-create` | Audit, validate, or create selected project knowledge documents |
| `custom [--role ROLE]` | Custom skills (`skill_file` to read) and agents (`prompt`, `tools`, `model`) from `[custom]` in effect, optionally only those a role receives, plus scope `overrides` |
| `onboard-status` | Report whether the project is onboarded (shared config plus a correct ignore boundary) and which optional add-ons (Jira, GitLab, GitHub, MCP, tester, hooks, worktree setup, and so on) are not configured yet |
| `shape-assess` / `shape-create` / `shape-reflect` | Assess, create, or reflect on requirements shaping artifacts |
| `hook NAME` | Run a Claude Code hook handler (`lint_after_edit`, `shaping_ripple`, `capture_completed_plan`, `validate_plan_stop`) on the hook event JSON from stdin; installed by `meow hooks install claude` |
| `orchestrate COMMAND ...` | The helpers behind `/meow:orchestrate`; every command and its JSON is listed under "The orchestrate helpers" below |

`capture_completed_plan` runs on PostToolUse with the `Write|Edit` matcher;
`validate_plan_stop` runs on Stop.

`prompt` roles: `planner`, `generator`, `explorer`, `reviewer-plan`,
`reviewer-prompt`, `reviewer-mr`, `reviewer-branch`, `review-fixer`,
`lint-fixer`.

`verify` reports tester test commands, dev-server and MCP launcher readiness,
test directories, and architecture document availability. `ready_unchecked`
means the local launcher exists; it does not prove that tests pass or a remote
connection works. Secret environment values are never included. Missing
architecture files are optional context.

## The orchestrate helpers

`meow native orchestrate COMMAND` supervises one orchestrator (see the
`orchestrate` skill): a graph of tasks, each an unattended `meow run` sprint in
its own detached child process, merged into the branch `orchestrator/ID` and
delivered as one pull request. The helpers call the same functions as
`meow orchestrate`, print one JSON document, and start no agents; the session
plays the judgement roles. Every command takes `--work-dir`. Commands that
change state hold the **native driver lock**: each call refreshes its heartbeat,
another native call may take it over, a lock without a heartbeat for 10 minutes
is reclaimable, and a live CLI driver makes them fail with a "driven by a live
cli driver" message. `status` never takes the lock. `ID` is the 12-character
orchestrator ID.

| Command | Purpose and JSON |
|---|---|
| `init (--objective TEXT \| --tasks FILE) [--from B] [--max-parallel N] [--test] [--no-pr]` | Needs a clean checkout. Creates the orchestrator and its integration worktree and takes the lock. Returns `id`, `integration_worktree`, `state_file`, `tasks_file`, and `decomposer` (`system_prompt`, `query`, `output_file`; `null` when a plain tasks file already defines the tasks, which makes the phase `running`). A tasks file whose every entry has a `source_ref` (a Jira key) is a Jira set and still gets a decomposer, which may only add `depends_on` and `expected_paths` |
| `tasks ID --file F` | Validate and save the decomposer's output: `{ok: true, tasks}` (plus `warning` when F differs from the given output path) or `{ok: false, error, retry_allowed}`. Missing files also allow one corrective retry; the second rejection fails the orchestrator |
| `tick ID` | One supervisor step: poll children, commit and merge passed tasks, launch ready ones. Returns `state` (`waiting`, `conflicts`, `failures`, `all_merged`, `blocked`, `cancelled`), `launched`, `merged`, `running`, `conflicts` (`task`, `paths`, `prompt` with `system_prompt` and `query`) and `failures` (`task`, `cause`, `evidence`, `prompt` with `system_prompt`, `query`, `output_file`). Releases the lock on `blocked` and `cancelled` |
| `wait ID [--timeout S]` | Blocks until a running child's run journal or the cancel marker changes, or `S` seconds (default 300) pass: `{changed, elapsed}`. Run it as a background command instead of polling |
| `merge-done ID --task T` | Finish a conflicted merge after the files were resolved: `{task, status, commit, paths}` with `status` `merged`, `noop`, `conflict` (conflict markers remain in `paths`), or `checks_failed` (rolled back; the task returns under `failures`) |
| `merge-abort ID --task T` | Roll the merge back and send the task to triage as an `integration` failure: `{task, status: "aborted"}` |
| `triage ID --task T --file F` | Validate and apply a triage decision file: `{task, action, status, pending, phase}`. An invalid decision blocks the orchestrator (`action: "invalid"`) |
| `review ID` | Objective review round: runs the required checks in the integration worktree (committing earlier review fixes first) and returns `round`, `max_rounds`, `checks_passed`, `check_failures`, `system_prompt`, `query`, `review_file`, `model`, `tools` for a fresh reviewer subagent |
| `review-result ID --file F` | Record the verdict file (parsed like every other verdict; a missing file or failing required checks mean FAIL): `{verdict, round, rounds_left, phase}`. PASS moves to `delivering`; the last FAIL blocks with cause `objective-review` |
| `deliver ID` | Push the integration branch (idempotent): `{branch, base, title, body, provider, manual_link, open_pr, skip_reason}` for the session to open the PR or MR |
| `pr ID (--url U \| --error E)` | Record the PR outcome and finish as `delivered`: `{phase, exit_code, report}`. Exit codes: 0 delivered with a PR (or PR disabled), 3 delivered without one, 1 failed, 2 blocked, 130 cancelled |
| `status [ID]` | The whole journal plus a `summary` (the latest orchestrator when `ID` is omitted) |
| `cancel ID` | Cancel the live children and the orchestrator |
| `release ID` | Drop the native driver lock |
| `resume ID [--task T] [--request TEXT]` | Prepare a stopped orchestrator to be driven again, without driving it: rolls back a half-finished merge, resets a blocked task (cycles to 0, optional new request), returns `failed` orchestrators to the phase that failed, and retries a failed PR. Returns `id`, `phase`, `reset_merge`, `decomposer` |

## Resolved workflow and agent definitions

`prepare` applies configured worktree setup before returning a newly created
worktree, including branch and existing-branch worktrees. Failed setup retains
the worktree and is retried on preparation. Ordinary reuse does not repeat
successful setup commands. Missing Node dependencies are installed with the
project's package manager on new/reused worktrees and `--no-worktree` runs;
commands obey the `setup` permission role. The returned `setup` array reports
completed setup and dependency actions. An error stops preparation, so do not
dispatch agents after a failed `prepare` or clean up its retained worktree.

`meow native workflow NAME` inspects a named workflow; execute its steps with
`meow run --workflow NAME`. Native helpers do not drive custom workflow trees.
Run `meow native prompt ROLE` for current resolved instructions and effective
model, including overrides from local, project, and user agent directories.
Its `tools`, `skills`, `agents`, and `session` fields describe the resolved
definition. Honor its tools and applicable skills when playing or briefing that
role. Agent overrides replace whole definitions; code-owned gate contracts and
permission policy still apply. See `docs/WORKFLOWS.md` for all template values,
scopes, and catalog commands.

## Tester mode

`/meow:run --test` and `/meow:review --plan PATH --test` use the matching
CLI flow so configured servers stay alive during the tester agent's run.
Before native run preparation, inspect `meow native verify --work-dir PATH`.
If `tester.configured` is true, use the CLI run flow even without `--test`:
configured tests automatically enable the CLI tester, and native run has no
tester agent lifecycle. Preserve resume/run IDs and supplied plan/Jira options
when switching modes. A native checkpoint has no Claude transcript IDs; use
`meow resume ID --continue --repair` to start fresh CLI agent sessions from
its saved plan. Review accepts `--test`
only with an explicit plan file and rejects other sources and `--review-file`.

## Roles

| Role | Who does it in native mode |
|---|---|
| Orchestrator | You, following the skill |
| Planner | You. Invoke `superpowers:writing-plans`, but meow's constraints win: write the plan to the `plan_file` from `prepare`, include a `## Sprint Contract` of concrete pass/fail criteria, do not use the skill's default location, do not stop for its execution-method handoff. Run `meow native prompt planner --plan <plan_file>` and honour it. |
| Generator | You. Invoke `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging` (on unexpected failures), `superpowers:receiving-code-review` (before acting on findings) and `superpowers:verification-before-completion`. Follow `meow native prompt generator --plan <plan_file>`: stay inside the Sprint Contract, do not commit, do not grade your own work. |
| Reviewer | **A fresh subagent every round** (Agent tool, `general-purpose`), never you: it must not share your context. See below. |
| Explorer | Optional read-only subagent (Agent tool, `Explore` or `general-purpose`) for research whose raw output you do not need in full. Use `meow native prompt explorer` as its instructions and its returned `model` when not null; null inherits the host default. Never dispatch the raw `prepare.models.explorer` SDK setting. |
| Review fixer | The shipped CLI definition has an empty skills list; overrides may add skills. It uses a scoped fixer prompt and a post-edit lint hook. In native review fix flows, run `meow native prompt review-fixer`, verify findings before editing, make the smallest in-scope correction, and run per-file lint after edits. |
| Lint fixer | The shipped CLI definition has an empty skills list; overrides may add skills. It uses a lint-specific prompt and a post-edit lint hook. In the native lint skill, run `meow native prompt lint-fixer` and run per-file lint after edits. Do not start a second agent. |
| Decomposer | You, in `/meow:orchestrate`. `meow native orchestrate init` returns the exact decomposer `system_prompt` and `query` (the same text the CLI role gets); follow them with read-only tools and write only the `output_file` it names. For a Jira set keep every issue as its own task. |
| Triage | You, in `/meow:orchestrate`. Each `failures` entry from `tick` carries the triage `system_prompt`, `query`, and `output_file`. Read the evidence files, write the decision JSON, and change no code. |
| Integrator | **A fresh subagent per merge conflict** (Agent tool, `general-purpose`), briefed with the `conflicts` entry's `prompt`. It may use only Read, Grep, Glob, Edit, and Write, edit only the conflicted `paths` in the integration worktree, and never run git. |
| Objective reviewer | **A fresh subagent every round** (Agent tool, `general-purpose`), briefed with the `system_prompt`, `query`, and `tools` from `meow native orchestrate review`; it writes the verdict to the `review_file`. The session then acts as the review fixer in the integration worktree. |
| Jira issue fetcher | CLI mode reads through the configured Jira MCP server. Native run/review skills use the already-connected Jira MCP tools directly and pass the issue text to the planner or reviewer. |
| GitLab merge-request fetcher | CLI mode reads through the configured GitLab MCP server. Native review uses the already-connected GitLab MCP tools directly to fetch the title, description, and diff, then dispatches `reviewer-mr`. |
| GitHub pull-request fetcher | CLI mode reads through the configured GitHub MCP server. Native review uses the already-connected GitHub MCP tools directly to fetch the title, description, and diff, then dispatches `reviewer-mr --provider github`. |

### Custom skills and agents

When you play a role (planner, generator, review fixer, lint fixer) or brief a
reviewer or explorer subagent, run `meow native custom --role <role>` first
(use `reviewer` for every reviewer prompt, and underscores in role names).
For each returned skill, read its `skill_file` and follow it whenever its
description applies; pass the same files to a subagent you brief. Each
returned agent is a delegate you may dispatch with the Agent tool
(`general-purpose`): its prompt is the agent's `prompt` plus the task, its
model is `model` when not null, and tell it to use only its listed `tools`
(`inherit` means your own, never the Agent tool). The CLI enforces the same
tool cap and `[permissions]` policy; in native mode you must respect them.

### Dispatching the reviewer

1. Run `meow native prompt reviewer-plan --plan <plan_file> [--focus "<text>"] [--worktree]`
   (or `reviewer-prompt` / `reviewer-mr`; see each skill). Pass `--worktree` when
   `prepare` reported `use_worktree: true`. Add `--active-dir` when working in a worktree.
2. Launch one Agent-tool subagent. Its prompt is the JSON's `system_prompt`
   followed by a blank line and the `query`. Tell it the working directory
   (`active_dir`) and that it must write its verdict to the `review_file` from
   the JSON. Use the JSON's `model` if it is not null.
3. When a local reviewer returns, inspect `meow native review-checks --status` with the same
   directory flags. If the evidence is missing, stale, or interrupted, run
   `review-checks --background` and wait for completion before reading the
   verdict. Pending checks are infrastructure work, not a failed reviewer
   round; do not burn fix rounds on missing verification metadata.
   If source/config inputs changed after the reviewer prompt, prepare a new
   prompt and dispatch a fresh reviewer against the current inputs; running
   checks alone cannot refresh an old review binding.
   For every review, run `meow native verdict <review_file>`. Trust the file
   and the command, not the subagent's chat summary. A missing file or missing
   `STATUS:` line counts as FAIL.

## Lint discipline (replaces the SDK post-edit hook)

- After **every** Write/Edit you make to a source file, run
  `meow native lint --file <path> --active-dir <active_dir>`. If `clean` is
  false, fix the reported `problems` before moving on (the command already
  applied each command's auto-fix flag).
- Before **every** review, run `meow native lint --active-dir <active_dir>`
  (project-wide, check-only) and fix everything in `blocking`. Then build the
  reviewer prompt: its `query` already carries the current lint evidence, so
  the reviewer does not rerun lint; `informational` findings never block.
- Before building a local reviewer prompt, run `meow native review-checks
  --background --work-dir <project-path> --active-dir <active_dir>` and wait
  for `review-checks --status` to report current completed evidence. Reuse
  unchanged passing evidence. Fix actual blocking check failures before the
  reviewer; a running or interrupted check never counts as PASS.

## Round limits

The counter lives on disk, next to the plan, so it survives context
compaction. **Never decide from memory whether rounds remain.**

- A *round* = one generate/fix step followed by one review (CLI parity).
  A review-first flow counts its opening review as round 1.
- Call `meow native round <plan_file> [--active-dir ...]` at the start of each
  round. If the JSON says `"exhausted": true`, **stop**: do not generate or
  review again. Report that the plan did not pass after `max_rounds` rounds and
  give the review file path (the CLI's own failure message).
- Start of a fresh sprint (new or re-planned plan): `round <plan> --reset`.
  When resuming or re-reviewing an existing plan, do not reset.

## Review loop (used by the native `run`, `review`, and `lint` workflows)

```
repeat:
  round <plan>            -> stop if exhausted
  generate / fix          (skipped in review-first flows until the review fails)
  lint (project-wide)     -> fix blocking findings
  review-checks           -> wait for current required verification; fix failures
  reviewer subagent       -> verdict
  PASS -> done            FAIL -> feed the review file's findings into the next
                                   generate/fix step (verify each finding first;
                                   report unsupported or out-of-scope ones)
```

The generator step after a FAIL receives: "The reviewer found issues. Fix them,
then stop." plus the full review text (same wording as the CLI).

## Files and conventions (identical to CLI mode)

- Plan: `<docs_dir>/<name>.md` (or `plan.md`); review of it: `<name>-review.md`.
  The default `docs_dir` is `.meow/plans`.
- Other reviews: `<flavor>.<id>.review.md` (flavor `prompt`, `branch`, `gitlab`
  or `github`; `<id>` random hex). Always use the `review_file` path that
  `meow native prompt` returns; never guess the name.
- Verdict format: line 1 `SUMMARY: ...`, line 2 `STATUS: PASS` or `STATUS: FAIL`,
  then one line per criterion.
- Never edit files under `docs_dir` other than the plan and the review verdicts.
  Native `finalize` commits and pushes only after verification in a separate
  linked worktree; in-place native runs do not deliver automatically.
- Worktree hygiene: when `use_worktree` is true, implementation changes go in
  `active_dir`. Automatic project onboarding uses the original checkout as
  described below.
- `meow native prepare` onboards the project (a documented `.meow/config.toml`,
  a comment-only `.meow/config.local.toml` when missing, and the `.gitignore`
  boundary, in the original project checkout) when it was never onboarded, and reports it in the
  JSON's `onboarding` field; mention any `onboarding.files` to the user. Existing
  config files are never overwritten. Do not invent lint commands;
  `/meow:onboard` handles add-ons.

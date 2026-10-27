# Native mode: shared protocol

Read this for native execution. `plan`, `review`, and `lint`
default to native; `run` defaults to CLI. You execute native roles in this
session or fresh subagents. Orchestrator task sprints remain detached CLI
workers. Named workflows, including `quick`, execute through CLI
([WORKFLOWS.md](../../docs/WORKFLOWS.md)).

Future-time requests use [schedule](../schedule/SKILL.md) before starting work.
Configuration and definition formats live in [INTEGRATIONS.md](../../docs/INTEGRATIONS.md)
and [WORKFLOWS.md](../../docs/WORKFLOWS.md); load relevant sections as needed.

## The `meow native` helper

Helpers start no agents and print JSON on stdout; errors print one JSON line
on stderr. Exit 75 (`"retryable": true`): wait `retry_after` and rerun the same
command, at most `[run] auto_retries` (default 3) consecutive 75s without
progress. Exit 1, or retries spent: report the error and stop dependent work.
If `meow` is missing,
point to the README installer; do not guess a venv path. Use `COMMAND --help`
for flags. Pass `--work-dir PATH` (also `--working-dir`/`-d`) for the project
root and `--active-dir PATH` where applicable for the prepared worktree. Keep
these directories consistent across calls.

| Command | Purpose |
|---|---|
| `prepare` | Guards, onboarding, setup, worktree; retain returned `name`, `active_dir`, `use_worktree`, `docs_dir`, `plan_file`, `review_file`, `max_rounds` |
| `verify` | Config/launcher readiness without starting tests, servers, agents, or MCP; `ready_unchecked` is not execution/connectivity proof |
| `workflow NAME` | Resolved workflow scope, fingerprint, and expanded steps |
| `latest-plan` / `latest-review` | Find artifacts in `docs_dir`; review JSON includes source `flavor` |
| `prompt ROLE` | Resolved SDK instructions, query, model, tools, skills, delegates, session; `reviewer-mr` returns a `query_template` instead of a query |
| `custom --role ROLE` | Applicable custom skills, capped delegates, scope overrides |
| `lint` | Project check; `--file PATH` auto-fixes one file, `--fix` auto-fixes project-wide, `--all-blocking` treats advisory commands as blocking |
| `review-checks` | Required verification; `--background` starts a durable job, `--status` reads state and log/result paths |
| `verdict FILE` | Validate the report and current verification/coverage bindings; returns status/summary |
| `round ANCHOR` | Advance durable counter; `--show` inspects, `--reset` starts fresh |
| `checkpoint PHASE` | Atomic transition; omit `--run-id` only to create a run (pass `--intent run` or `plan`), then reuse the returned ID; `parked --failure TEXT` records a crash |
| `finalize RUN_ID` | Revalidate evidence and required gates, then deliver when eligible |
| `push BRANCH` | Push a named branch to origin |
| `onboard-status` | Base setup state and optional add-on gaps |
| `run COMMAND` | Supervisor helpers; see below |

`prepare` applies setup before returning. Failed setup retains the worktree for
retry; successful setup is not repeated on reuse. Missing Node dependencies are
installed on new/reused worktrees and in-place runs under the `setup` permission
role. Do not dispatch after preparation fails. Report `onboarding.files`;
existing config is preserved and onboarding edits stay in the original checkout.
Implementation belongs in `active_dir`. Do not invent lint commands;
`/meow:onboard` handles add-ons.

## Dependency installs

Fix missing modules, browsers, or install timeouts yourself under
[the dependency rules](dependencies.md); never switch Bun to `hoisted`.

## Roles

Obtain role prose from `meow native prompt ROLE`; do not copy it into skills.
Honor returned tools and applicable skills. Run `meow native custom --role ROLE`
for custom skills/delegates (`reviewer` for all review variants; underscores in
config role names). Read applicable `skill_file` paths and pass them to subagents.
Delegate tools cannot exceed the parent's or include `Agent`; `inherit` means
the parent's tools. Whole-file scoped overrides cannot bypass code-owned gates
or permissions.

Pass the actual `--host` (for example `--host codex`) to `prompt`, `custom`,
and `run review` on non-Claude hosts. Repeat `--available-model ID`
only for exact supported IDs. Use returned `model` when non-null; null inherits
the host model. Surface `model_fallback_reason`; never guess provider model
mappings. Use the host's subagent API when tool names differ.

| Role | Native execution |
|---|---|
| Planner (`planner`) | You. Use `superpowers:writing-plans`, but write to returned `plan_file`, include numbered tasks and a `## Sprint Contract` with concrete pass/fail criteria, and skip its execution-method handoff. |
| Generator (`generator`) | You. Use `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:verification-before-completion`; use `superpowers:systematic-debugging` on failures and `superpowers:receiving-code-review` before fixes. Stay within the contract; do not commit or grade your work. |
| Reviewer | Fresh subagent every round; use `reviewer-plan`, `reviewer-prompt`, `reviewer-mr`, or `reviewer-branch` as the skill directs. |
| Explorer (`explorer`) | Optional read-only subagent; use resolved prompt/model, never raw `prepare.models.explorer`. |
| Review fixer (`review-fixer`) | You; verify findings and make the smallest in-scope edits. |
| Lint fixer (`lint-fixer`) | You; fix lint without a second agent. |
| Decomposer / Triage | You; read-only except the output/decision file named by the orchestrator prompt. |
| Integrator | Fresh subagent per conflict; only Read/Grep/Glob/Edit/Write, only listed paths, never git. |
| Objective reviewer | Fresh subagent every round with returned orchestrator instructions/tools. |
| Jira issue fetcher / GitLab merge-request fetcher / GitHub pull-request fetcher | Native uses connected MCP tools; CLI uses configured servers. |

## Lint discipline and reviewer dispatch

1. After every source Write/Edit, run `meow native lint --file PATH` in
   `active_dir`. Fix reported problems before proceeding.
2. Before a local reviewer prompt, run project-wide `meow native lint` and fix
   `blocking` findings; `informational` findings do not block. The prompt
   carries lint evidence so the reviewer need not rerun it.
3. Run `meow native review-checks --background` with the same directory flags.
   Inspect `review-checks --status` until current required lint/test/build
   evidence is complete. Reuse unchanged passing evidence. Pending/interrupted
   checks are infrastructure work, not a failed reviewer round.
4. Build the reviewer prompt. Pass `--worktree` for `use_worktree: true` unless
   the skill excludes it. Brief a fresh subagent with `system_prompt`, a blank
   line, `query`, `active_dir`, allowed tools, and returned `review_file`.
   For remote reviews fill `query_template` with fetched values.
5. Run `meow native verdict <review_file>` after the reviewer returns. Trust
   file validation, not its chat summary. Missing/malformed verdicts fail.
   Local PASS requires current checks and requested scope coverage; remote
   reviews use the supplied diff without local checks.

If source/config changes after the prompt, rerun stale checks and dispatch a
fresh reviewer with a new prompt; checks alone cannot refresh a report binding.
Preserve verification/coverage sidecars with the report. Finish scoped edits
before final checks; do not add audit passes while they run. Keep one durable
job ID: a lost shell handle is not a failed job. If a run exceeds its baseline,
inspect logs, child start times, and source/config changes for retries. Restart
only for confirmed stale/failed evidence. Isolate artifact-writing tests; preserve user files.

## Round limits and review loop

A round is one generate/fix step followed by review; an opening review counts
as round 1. Keep one plan/report anchor. At each round start call
`meow native round ANCHOR`; stop on `exhausted`. Reset only for a new or
replanned sprint, never on resume. Use `--show`, not memory, to inspect.

Generate/fix (skip until FAIL in review-first flows), lint, run required checks,
dispatch a fresh reviewer, validate. PASS ends the loop. FAIL supplies the full
review text to the next fix step: "The reviewer found issues. Fix them, then
stop." Verify findings; report unsupported/out-of-scope ones. At the limit,
report `max_rounds`, remaining findings, and review path.
Failed checks skip reviewer dispatch and feed command output to the next fix round.
Reuse current evidence after report-only writes; source edits rerun checks.

## Checkpoints, tester mode, and delivery

Checkpoint before/after planner, generator, reviewer, tester, and required-check
phases. Use `interrupted_mutation` for an interrupted edit; inspect and review
the actual worktree against the saved plan before retrying. Keep failed or
interrupted worktrees. Inspect journals with `meow status RUN_ID` or
`meow resume RUN_ID`; continuation requires `--continue`. `meow status RUN_ID
--json` gives `retryable`, `next_command` and `not_resumed_reason`; `meow resume
--parked` continues every parked run within budget; never resume a
`--intent plan` run.

Before native run preparation, inspect `meow native verify`. `--test` or
`tester.configured: true` routes runs to CLI so configured servers stay alive
throughout tester validation; native execution cannot own that lifecycle.
Configured tests auto-enable it for behavior changes when workflow `auto_test` is true;
quick requires `--test`. `--force-disable-tests` also routes to CLI: forward it to
disable tester/test/browser gates and servers while keeping lint/build.
Preserve plan/Jira/worktree options and saved IDs. Continue a native checkpoint
with `meow resume RUN_ID --continue --repair` because it has no Claude session
IDs. Review tester mode requires an explicit plan:
`meow review --plan PATH --test` (optionally `--fix`); other sources, implicit
discovery, and `--review-file` reject it. Never invent tester evidence.

Completion requires reviewer PASS, enabled tester PASS, and current required
gates. PASS checkpoints bind the report fingerprint; finalization checks it
again and routes configured tester projects to CLI even with `--tester PASS`.
Report success only when `finalize` returns `complete: true`.
Separate-worktree finalization commits and pushes; in-place runs do not
automatically deliver.
For worktree delivery, the first checkpoint saves `--target` or
`[delivery].target_branch`, never `--from` (which only chooses the start).
State the saved MR target from `meow status RUN_ID`, also on resume.
The loop is finished and final delivery is being verified through
the MR pipeline: open the MR/PR with connected tools and report the pipeline
status. The pipeline-fix loop is CLI-only ([pipeline](../../docs/PIPELINE.md));
claim "Verified by pipeline" only when it actually passed.

Claude owns transcripts; journals retain role/session
references, and missing SDK usage is `unavailable`.
Successful finalization cleans integrated task artifacts and retains the result.
`meow cleanup [RUN_ID]` retries cleanup; preserved workers include a reason.
Worker runtime evidence is archived under `.meow/cleanup/` before removal.

Use artifact paths returned by helpers, never reconstruct names. Default
`docs_dir` is `.meow/plans`. Verdicts start with `SUMMARY: ...`, then
`STATUS: PASS` or `STATUS: FAIL`, followed by criterion evidence. Edit no other
files under `docs_dir`.

Only cancel processes whose recorded run ID or handle proves exclusive ownership
by the current run. Never kill by name/pattern; leave uncertain ownership alone.

## The multi-sprint run helpers

Load [the run supervisor procedure](../run/references/orchestration.md)
only for explicitly requested native multi-sprint supervision;
it defines the sequence, returned fields, and state transitions.
Commands take `--work-dir`. Mutations refresh the native driver lock;
`status` is read-only. A live CLI driver blocks native mutations; a native
heartbeat older than 10 minutes is reclaimable. IDs are 12 characters.

| Command | Operation |
|---|---|
| `init` / `tasks` | Create integration worktree / validate decomposition (one corrective retry) |
| `tick` / `wait` | Advance supervision / wait for child journal or cancellation changes |
| `merge-done` / `merge-abort` | Validate conflict resolution / roll back and triage |
| `triage` | Validate/apply decision file |
| `review` / `review-result` | Run checks and prepare independent review / record verdict |
| `deliver` / `pr` | Push and return PR metadata / record creation outcome |
| `status` / `cancel` / `release` | Inspect / cancel children / release native lock |
| `resume` | Recover without driving; return phase and any decomposer prompt |

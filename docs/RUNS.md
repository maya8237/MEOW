# Runs, worktrees, and recovery

Use [CLI.md](CLI.md#build-and-plan) for command syntax and
[WORKFLOWS.md](WORKFLOWS.md) for custom execution trees.

## Preparation and planning

`run` and `plan` use isolated worktrees. Fresh plans get unique names
(`NAME-2`, `NAME-3`, …); `--plan`, `--resume-at review`, and saved-run
continuation reuse existing work and take priority over `--from`.
In-place `--no-worktree` execution makes the name optional.

Runs, Jira runs, and agent-driven lint-fix require a clean checkout except
`.gitignore`/`.meow/`, unless a worktree is created from explicit `--from`.
Plan and review permit pending changes. Manual plan approval (`-m`) displays
the plan before generation; declining starts no generator. Plan-only rejects it.

First config-dependent use repairs the ignore boundary and creates a minimal
shared config with detected ruff/eslint lint, without prompting or overwriting
config. Onboarding failures do not fail the command. Changes stay in the
original checkout, outside feature/task branches; editable-install
`*.egg-info/` is locally ignored via Git's `info/exclude`.
Use `/meow:onboard` for add-ons.

Runs never wait for answers except at explicit `-m` plan approval. Feature
flows do not propose/edit prose docs.

With no saved run, `--resume-at review --plan PATH` skips planning and reviews
existing code first, generating only after FAIL. Omit the path to discover the
latest plan. Default `--resume-at generate` replans unless a plan is supplied.

## Verification and delivery

Built-in workflows implement, review, and fix until review passes or the
configured round limit is reached. A passing review is followed by final checks
before delivery. Custom workflows control their own loop stages.

Completion requires an independent reviewer PASS, enabled tester PASS, and
current required lint/test/build evidence. Advisory failures remain visible.
Normal runs commit and push to `origin` from their worktree.
`-n` uses the selected checkout without committing or pushing; `-nc` uses it
and commits only; `-np` uses it and commits/pushes after verification.
Explicit `-n` also disables delivery inside an existing linked worktree.
Detached work gets a result branch.
A missing remote or failed push retains the worktree/checkpoint.

`--background` detaches a run and rejects manual approval and lint-fix.
`--from`/`-b` selects the starting commit for a new worktree.
Select an existing linked worktree with `--work-dir PATH`; MEOW uses it directly.

## Status and continuation

```bash
meow status [RUN_ID] --verbose
meow resume RUN_ID
meow resume RUN_ID --continue
meow cancel RUN_ID
```

Atomic journals live under ignored `.meow/runs/`. Resume is inspection-only
until `--continue`; continuation validates repository, worktree, branch, plan,
config, and workflow. `--repair` re-baselines blocked gates, including missing
sessions. Task graphs rerun only unfinished tasks. Interrupted mutations review
the saved worktree against its plan before any generator retry; mismatches stop
with a diagnostic. Preserve failed/interrupted worktrees and evidence.


Status shows received SDK turns, tokens, and USD cost; verbose lists results
by role, with missing metrics marked `unavailable`. Claude owns transcripts;
MEOW stores only role/session references.

### Managed runs

`run` and `resume --continue` exit 0 when settled, 75 when parked or retryable
(stderr `MEOW_RETRY {"run_id", "next_command", "retry_after"}`), 1 when blocked
or out of budget (`MEOW_BLOCKED {"run_id", "cause", "message", "stop"}`), 3 when
pushed but the MR failed, and 130 when interrupted. `meow status ID --json`
reports `phase`, `settled`, `retryable`, `cause`, `next_command`, `attempts`,
`budget_left`, `worker`, `intent` and `heartbeat_age`, plus `not_resumed_reason`
when no resume will be tried. A parked run whose supervisor is alive reports
`settled: false`, `supervised: true` and no `next_command`: it resumes itself.
A failure before any run record exists exits 75 only for a recognised transient
class (timeout, SDK process, OS I/O or resource); anything else exits 1. A
manager retries at most `[run] auto_retries` consecutive 75s that show no
progress, then reports the failure. Native
helpers follow the same rule and print `{"error", "retryable", "cause",
"retry_after", "command", "next_command"}` on stderr; on 75 run `next_command`
when set, else rerun the same command. A network push failure (timeout, DNS,
reset, 5xx, stale lock) parks the run; auth, rejected or hook-declined pushes
and unrecognised git errors end `delivery_failed`.
`native checkpoint parked --failure TEXT` records a crash the session saw;
`--intent run|plan` marks plan-only runs, which are never parked or resumed.
Every helper with a run ID refreshes its heartbeat.

A managing session starts runs with `--background` and polls `meow status ID --json`
(cron `*/7 * * * *`), acting only when `settled` is true: with `retryable: true`
and `budget_left > 0` it runs `next_command` detached; with `retryable: false`
(or `not_resumed_reason` set) it reports `cause`, `last_failure` and evidence, and
stops. On exit 75 it waits `retry_after`, up to `managed_resumes`, stopping on no
progress (`attempts`, `no_progress`). A `driver_busy` retry means a live driver owns
the run: keep watching, do not count it. Orchestrator status JSON has only `phase`,
`settled`, `retryable` and `next_command`: `blocked` waits for a person, and
`failed` retries only after a non-blocking error. `meow resume --parked` does the same unattended
and inline; run it detached or hourly via OS scheduling
([skills](../skills/schedule/SKILL.md)). Exit 75 includes bare transient
OS errors, so the retry cap lives in the managing session, not the exit code.

Cancellation is cooperative between phases and during agents/checks. It stops
owned tasks, records `cancelled`, and preserves output/worktree/resume identity;
it does not signal unrelated PIDs. Explicit continuation validates state and
reviews interrupted generation first.

## Background runs and queues

```bash
meow run "REQUEST" --name NAME --background
```

Returns a run ID immediately. Status supplies worker state/log path; a vanished
worker is marked interrupted with a resume command. Background runs cannot
wait for plan approval. Optional `[background].notify_command = ["notify-meow"]`
receives only run ID, terminal phase, and status command as extra arguments.
Notification failures are recorded separately from verification.

`meow queue "request"` adds work even while another run is active.
Bare `meow queue` processes FIFO under a repository worker lock; failed tasks
pause the queue for inspection/retry. It exits 75 while items stay parked;
`resume --parked` requeues them and prints `next_command` (`meow queue`) to run
them.

## Worktree setup and cleanup

`meow worktree list` and `inspect RUN_ID` show owned worktrees.
`clean RUN_ID` removes only a completed registered worktree under `.worktrees/`
when clean and with no unpushed commits. Detached branches/unknown push state
block cleanup; no force flag exists.

Setup and dependency installation apply before agents, including native,
plan-only, branch-review, and task flows. Failed setup retains its worktree
for retry. Missing Node dependencies are repaired on reused/in-place runs too.
See [WORKTREE_SETUP.md](WORKTREE_SETUP.md) for copy rules, permission gates,
package-manager selection, and Bun shared storage.

## Jira and lint-fix

`meow run --jira [KEY]` fetches the named or latest configured project issue,
builds in its own retained branch/worktree, and commits/pushes after PASS.
Success prints issue/branch JSON. Jira rejects `--name`, `--no-worktree`,
`--from`, `--resume-at`, and `--plan`; manual plan approval is interactive-only.
Configure the [Jira integration](INTEGRATIONS.md#jira).

`meow run --lint-fix` auto-fixes and uses a fixer up to `max_rounds`, in place,
with a clean-tree requirement. Adding `--report-only` prints findings without
edits or an agent; `/meow:lint` then fixes in the calling session.

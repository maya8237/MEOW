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

Before planning, project guidance/code are inspected; requests may be shaped
and complex UI/component work breadboarded. Clear requests proceed directly.
Unresolved product decisions checkpoint and stop; runs never wait for answers
except at explicit `-m` plan approval. Feature flows do not propose/edit prose docs; onboarding
can create selected project knowledge.

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

For a pre-planning `needs_user_decision` pause:

```bash
meow resume RUN_ID --continue --request "Complete clarified request"
```

This replaces the entire request, so include every surviving requirement.
The original request and worktree are preserved. Empty/unresolved text is
rejected without altering the journal. Other phases and inspection-only mode
cannot replace the request.

Status shows received SDK turns, tokens, and USD cost; verbose lists results
by role, with missing metrics marked `unavailable`. Claude owns transcripts;
MEOW stores only role/session references.

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
pause the queue for inspection/retry.

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

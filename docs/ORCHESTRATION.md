# Orchestrating parallel sprints

`meow run --orchestrate` decomposes one objective or Jira set into tasks, runs each
as an automatic detached sprint in its own worktree, merges passing tasks,
reviews the combined objective, then pushes one branch and opens one PR/MR.
Unrecoverable tasks or exhausted review rounds stop with nothing pushed.

Use [CLI.md](CLI.md#multi-sprint-runs) for commands; `/meow:run` selects this mode for independent deliverables.
Explicit native supervision uses the same engine. Configuration is under [orchestrator](INTEGRATIONS.md#orchestrator).

## Input and flags

Choose one objective, `--jira KEY...`, `--jql QUERY`, or `--tasks FILE`.
Select orchestration with `--orchestrate` (`-o`); task files, JQL, and multiple
Jira keys also select it. Starts require a clean checkout except
`.gitignore`/`.meow/`.

| Flag | Meaning |
|---|---|
| `--from BRANCH` | Base, default current branch; required for detached HEAD |
| `--max-parallel N` | Concurrency, default 3 from config |
| `--dry-run` | Save decomposition and stop; resume continues it |
| `--test` | Configured/exploratory testing in each task |
| `--no-pr` | Push without opening a PR |
| `meow resume ID --continue --task T --request TEXT` | Reset a blocked task (default blocker task), optionally replace request |

Jira is read-only and requires `[jira]`/`[jira.mcp]` in CLI mode.
Each issue stays one task; the decomposer may add only dependencies/paths.
Objective/Jira decomposition allows at most 32 tasks and one corrective retry
for invalid output. Narrow oversized objectives/queries.

## Task file

```toml
[[tasks]]
id = "schema"
request = "Add the audit_log table"

[[tasks]]
id = "endpoint"
request = "Add GET /audit"
depends_on = ["schema"]
expected_paths = ["src/api/audit.py"]
```

JSON equivalent: `{"tasks":[{"id":"schema","request":"..."}]}`.
IDs are case-insensitively unique, 1–64 letters/digits/`-`/`_`, starting
with a letter/digit. Dependencies name other IDs. Optional project-relative
`expected_paths` are hints and may overlap. Plain task files skip decomposition.

## Execution and recovery

Ready tasks start after all dependencies merge, most-depended-on first.
The integration branch `orchestrator/ID` lives in `.worktrees/orch-ID`;
tasks use `orchestrator/ID-TASK` in `.worktrees/orch-ID-TASK`, based on the
integration tip when each attempt starts. Passed tasks are committed and
merged with `--no-ff`.

An integrator may edit only conflicted files. Lint/checks run after each merge;
failing merges roll back to triage. Triage chooses retry, resume, rescope,
reintegrate, or blocked. A task and any split lineage share at most
`max_recovery_cycles` (default 3).

Once all tasks merge, objective review checks `base...orchestrator/ID`,
with fix rounds up to `review_rounds` (default 3). Only then is it pushed.
PR/MR title is `Orchestrator: FIRST OBJECTIVE LINE` (Jira sources list keys);
the report supplies the body. Provider/creation failure leaves the branch
delivered, emits `PR not opened: REASON` with a manual link, and exits 3.

Delivery prints
`{"orchestrator":"ID","branch":"orchestrator/ID","pr":"URL or null","tasks":N}`.
Other exit codes: 0 delivered with PR or PR disabled, 1 failed,
2 blocked without push, 130 cancelled. Usage is recorded per child and
summarized; no dollar budget is enforced.

## State, resume, and cleanup

Atomic state, tasks, report, cancel marker, and driver lock live under
`.meow/orchestrators/ID/`. Status shows phase, task states/cycles/branches/runs,
blocker, and PR. `meow status` adds an active-orchestrator roll-up and child
runs with source `orchestrator`; individual runs can use `meow resume`.

`meow resume ID` inspects; add `--continue` to execute. Resume preserves merged tasks, reattaches live children, rolls back an
interrupted merge, and retries failed delivery. A delivered run with a failed
PR retries only PR creation. Resolve blockers or reset the blocked request
with `--task`/`--request`. Cancel stops live children and records cancelled
without pushing.

One CLI/native driver owns the run; live rivals are refused. Native locks
without heartbeat for 10 minutes are reclaimable; either mode can resume the
other. `meow worktree clean ID` uses the 12-character orchestrator ID and
removes delivered worktrees only when clean, task branches are integrated,
and origin contains the delivered commit. Branches remain.

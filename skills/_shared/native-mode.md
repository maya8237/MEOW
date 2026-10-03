# Native mode: shared protocol

Every meow skill has two execution modes. This file defines **native mode**,
the default when a skill is invoked from inside Claude Code: *you* (this
session) do the planning, generating and orchestration, and you dispatch
reviewer/explorer work to Task subagents. No separate Agent SDK process is
started. **CLI mode** (the `meow <command>` shell-out in each SKILL.md's
"CLI mode" section) is used only when the user explicitly asks for it
("run it headless", "use the CLI", "separate process").

Both modes read the same `.harness.toml` and write the same files, so a run
started in one can be continued in the other (`meow review --fix`,
`meow run --resume-at review`, `/meow:review`, ...).

Release 2 run checkpoints are stored in `.meow/runs/`. Inspect one with
`meow status [RUN_ID] [--verbose]` or `meow resume [RUN_ID]`. The latter only
inspects until `--continue` or `--auto-resume` is supplied. Native execution
must write a checkpoint before and after planner, generator, reviewer, tester,
and required check phases; an interrupted generator turn is marked as a
possible mutation. Inspect and review the actual worktree against the saved
plan before another edit. Do not clean up failed or interrupted worktrees.
Completion requires current passing required lint, test, and build checks,
reviewer PASS, and enabled tester PASS. SDK usage missing from evidence is
`unavailable`.

## The `meow native` helper

`meow native <command>` prints one JSON document on stdout (failures: message
on stderr, exit 1). It starts no agents. If `meow` is not on PATH, tell the
user to install it (README: a venv with `pip install -e .`, or `pipx install -e .`)
and stop; do not guess at a venv path. Flags shared by every command:
`--working-dir PATH` (project root with `.harness.toml`; default cwd) and
`--active-dir PATH` (the worktree that `prepare` returned; default = working dir).

| Command | Purpose |
|---|---|
| `prepare [--name N] [--no-worktree] [--source-branch B] [--branch BR] [--existing-branch BR] [--allow-dirty]` | Startup guards + worktree; returns `active_dir`, `docs_dir`, `plan_file`, `review_file`, `max_rounds`, `models`, `lint` |
| `verify [--no-lint]` | Validate config and report lint, tester, and integration readiness without starting agents, servers, or MCP connections |
| `latest-plan` / `latest-review` | Newest plan / review file in `docs_dir` (`latest-review` also gives its `flavor`: plan, prompt or gitlab) |
| `verdict FILE` | `{status: PASS\|FAIL, summary}` of a review file |
| `lint [--file F] [--fix] [--all-blocking]` | Per-file (auto-fixing) or project-wide lint run. `--all-blocking` ignores `gate` and treats every command as blocking (what the `lint` skill needs; everything else wants the default gate/informational split) |
| `round PLAN [--reset\|--show]` | On-disk round counter; default advances it |
| `checkpoint PHASE [--run-id ID] [--request TEXT] [--plan FILE] [--review FILE] [--round N] [--reviewer PASS\|FAIL] [--tester PASS\|FAIL]` | Atomic run journal transition; omit `--run-id` only to create a native run, then reuse the returned ID |
| `finalize RUN_ID` | Run current required lint, test, and build gates and finish only when reviewer and enabled tester evidence pass |
| `prompt ROLE [--plan F] [--focus T] [--worktree]` | Exact SDK system prompt (+ task message, model) for a role |
| `push BRANCH` | Push a named branch to origin |
| `knowledge-audit` / `knowledge-check` / `knowledge-create` | Audit, validate, or create selected project knowledge documents |
| `shape-assess` / `shape-create` / `shape-reflect` | Assess, create, or reflect on requirements shaping artifacts |
| `hook` | Run an optional configured lifecycle hook |

`prompt` roles: `planner`, `generator`, `explorer`, `reviewer-plan`,
`reviewer-prompt`, `reviewer-mr`, `reviewer-branch`, `review-fixer`,
`lint-fixer`.

`verify` reports tester test commands, dev-server and MCP launcher readiness,
test directories, and architecture document availability. `ready_unchecked`
means the local launcher exists; it does not prove that tests pass or a remote
connection works. Secret environment values are never included. Missing
architecture files are optional context.

## Tester mode

`/meow:run --test` and `/meow:review --plan-file PATH --test` use the matching
CLI flow so configured servers stay alive during the tester agent's run.
Without `--test`, native skill behavior is unchanged. Review accepts `--test`
only with an explicit plan file and rejects other sources and `--review-file`.

## Roles

| Role | Who does it in native mode |
|---|---|
| Orchestrator | You, following the skill |
| Planner | You. Invoke `superpowers:writing-plans`, but meow's constraints win: write the plan to the `plan_file` from `prepare`, include a `## Sprint Contract` of concrete pass/fail criteria, do not use the skill's default location, do not stop for its execution-method handoff. Run `meow native prompt planner --plan <plan_file>` and honour it. |
| Generator | You. Invoke `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging` (on unexpected failures), `superpowers:receiving-code-review` (before acting on findings) and `superpowers:verification-before-completion`. Follow `meow native prompt generator --plan <plan_file>`: stay inside the Sprint Contract, do not commit, do not grade your own work. |
| Reviewer | **A fresh subagent every round** (Agent tool, `general-purpose`), never you: it must not share your context. See below. |
| Explorer | Optional read-only subagent (Agent tool, `Explore` or `general-purpose`) for research whose raw output you do not need in full. Use `meow native prompt explorer` as its instructions and `models.explorer` as its model. |
| Review fixer | The CLI role receives no additional skill metadata; it uses a scoped fixer prompt and a post-edit lint hook. In native review fix flows, run `meow native prompt review-fixer`, verify findings before editing, make the smallest in-scope correction, and run per-file lint after edits. |
| Lint fixer | The CLI role receives no additional skill metadata; it uses a lint-specific prompt and a post-edit lint hook. In the native lint skill, run `meow native prompt lint-fixer` and run per-file lint after edits. Do not start a second agent. |
| Jira issue fetcher | CLI mode reads through the configured Jira MCP server. Native run/review skills use the already-connected Jira MCP tools directly and pass the issue text to the planner or reviewer. |
| GitLab merge-request fetcher | CLI mode reads through the configured GitLab MCP server. Native review uses the already-connected GitLab MCP tools directly to fetch the title, description, and diff, then dispatches `reviewer-mr`. |

### Dispatching the reviewer

1. Run `meow native prompt reviewer-plan --plan <plan_file> [--focus "<text>"] [--worktree]`
   (or `reviewer-prompt` / `reviewer-mr`; see each skill). Pass `--worktree` when
   `prepare` reported `use_worktree: true`. Add `--active-dir` when working in a worktree.
2. Launch one Agent-tool subagent. Its prompt is the JSON's `system_prompt`
   followed by a blank line and the `query`. Tell it the working directory
   (`active_dir`) and that it must write its verdict to the `review_file` from
   the JSON. Use the JSON's `model` if it is not null.
3. When it returns, run `meow native verdict <review_file>`. Trust the file
   and the command, not the subagent's chat summary. A missing file or missing
   `STATUS:` line counts as FAIL.

## Lint discipline (replaces the SDK post-edit hook)

- After **every** Write/Edit you make to a source file, run
  `meow native lint --file <path> --active-dir <active_dir>`. If `clean` is
  false, fix the reported `problems` before moving on (the command already
  applied each command's auto-fix flag).
- Before **every** review, run `meow native lint --active-dir <active_dir>`
  (project-wide, check-only). Fix everything in `blocking`. `informational`
  findings are passed on to the reviewer's summary but never block.

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

## Review loop (used by sprint, meow-review, review-fix-review)

```
repeat:
  round <plan>            -> stop if exhausted
  generate / fix          (skipped in review-first flows until the review fails)
  lint (project-wide)     -> fix blocking findings
  reviewer subagent       -> verdict
  PASS -> done            FAIL -> feed the review file's findings into the next
                                   generate/fix step (verify each finding first;
                                   report unsupported or out-of-scope ones)
```

The generator step after a FAIL receives: "The reviewer found issues. Fix them,
then stop." plus the full review text (same wording as the CLI).

## Files and conventions (identical to CLI mode)

- Plan: `<docs_dir>/<name>.md` (or `plan.md`); review of it: `<name>-review.md`.
- Prompt review: `review.md`. Merge request review: `gitlab-review.md`.
- Verdict format: line 1 `SUMMARY: ...`, line 2 `STATUS: PASS` or `STATUS: FAIL`,
  then one line per criterion.
- Never edit files under `docs_dir` other than the plan and the review verdicts.
  Native `finalize` commits and pushes only after verification in a separate
  linked worktree; in-place native runs do not deliver automatically.
- Worktree hygiene: when `use_worktree` is true, every change goes in `active_dir`;
  the main checkout must stay untouched.
- If a `.harness.toml` is missing at the project root, tell the user and point
  at meow's `GUIDE.md`; do not invent lint commands.

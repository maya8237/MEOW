# Native in-session execution for meow skills

Status: draft for review. Branch: `claude/meow-skills-native-execution-f265a3`.

## Goal

When a meow skill is invoked from inside Claude Code, the calling session does
the work itself (plan, generate, review) using its own tools, Task subagents,
and superpowers skills, instead of shelling out to `meow`, which spins up
separate Agent SDK sessions the caller cannot see.

## Non-goals and hard constraint

The CLI/SDK path (`meow run|plan|review|cr|issue|gitlab-review|lint-fix|
review-fix-review`) is preserved exactly: same flags, same behavior, same
prompt text, usable headless with no Claude Code session present. Native mode
is additive. Skills are only ever run by Claude.

## Consistency contract (both modes)

Both modes read the same `.harness.toml` and produce interchangeable artifacts:

- Plan file `<docs_dir>/<name>.md` (or `plan.md`) containing a `## Sprint Contract`.
- Plan review `<plan-stem>-review.md`; prompt review `review.md`; MR review
  `gitlab-review.md`. Verdict format: first line `SUMMARY: ...`, next line
  `STATUS: PASS|FAIL`, one line per criterion. Default to FAIL when uncertain.
- Same worktree rules (`.worktrees/<name>`, detached for run/plan, named branch
  for issue), same clean-tree check, same `.gitignore` upkeep.
- Same `max_rounds`, lint plan (`per_file`, `gate`, `fix_flag`), and
  `docs/RULES.md` role sections.

So a run started natively can be continued by `meow review-fix-review` or
`meow run --resume-at review`, and vice versa.

## Architecture

### Roles

| Role | Native mode |
|---|---|
| Orchestrator | The calling session, following `SKILL.md` + shared protocol |
| Planner | The session, applying `superpowers:writing-plans` with meow's constraints |
| Generator | The session, applying executing-plans, TDD, systematic-debugging, receiving-code-review, verification-before-completion |
| Reviewer | A fresh Task subagent per round (independence: it must not share the generator's context) |
| Explorer | Read-only Task subagent on the configured `models.explorer` |
| Jira / GitLab fetch | The session's own connected MCP tools; nothing is launched from `[jira.mcp]`/`[gitlab.mcp]`. If unavailable, the skill reports that and stops |

### Mode selection

Each SKILL.md defaults to native. It uses the original shell-out (kept
verbatim as a "CLI mode" section) only if the user explicitly asks for
CLI/headless/separate-process execution.

### `meow native` helper (agent-free, JSON on stdout)

A new subcommand group. It imports existing modules (`config`, `worktree`,
`lint`, `rules`, `reviewer._verdict_status`, orchestrator lookups); it never
imports or starts the Agent SDK for its own work.

- `prepare [--name N] [--no-worktree] [--source-branch B] [--branch B]`:
  same guards as the CLI (`_ensure_clean_tree`, `_boot_repo`,
  `_resolve_working_dir` / `_ensure_branch_worktree`). Returns `active_dir`,
  `docs_dir`, `plan_file`, `review_file`, `max_rounds`, `models`, lint plan,
  per-role rules text, `use_worktree`.
- `latest-plan`, `latest-review`: the CLI's own lookups, with flavor detection.
- `verdict FILE`: `{status, summary}` via the shared parser.
- `lint --file F`: run per-file commands with fix flags (the SDK hook's
  logic); `lint --project`: check-only gate run; both report failures as the
  same `$ <command>\n<output>` blocks. `lint --report-only` semantics of the
  existing skill are subsumed.
- `round PLAN --next`: see State.
- `push`: push the current issue branch (reuses `_push_branch`).
- `prompt ROLE [--plan F] [--focus TEXT] ...`: prints the role's prompt.

Non-zero exit codes carry the same error messages the CLI raises.

### Single source of truth for prompts

Move prompt construction out of `agents/*` classes into pure functions in
`src/meow/prompts.py` (planner, generator, explorer, reviewer x3, review-fixer,
lint-fixer). The agent classes call them; `meow native prompt` prints them.
Tests pin that the strings used by the CLI path are byte-identical to today's.
Native reviewer subagents are dispatched with exactly this text, so the modes
cannot drift.

### State without a Python process

Everything durable is on disk: the plan, review files, and the git tree. The
only counter, the round number, lives in `<plan-stem>.native-state.json`
in `docs_dir`. `meow native round PLAN --next` increments it and returns
`{round, max_rounds, exhausted}`. The skill must stop and report when
`exhausted` is true, so limits survive context compaction. `.json` keeps the
file out of the `*.md` globs used by plan/review lookup. A fresh sprint resets
the counter; `--resume-at review` semantics are preserved by the skill
starting with review before any generation.

### Loop (sprint / meow-review / review-fix-review)

1. `prepare` (+ clean-tree/worktree handling).
2. Plan if needed (session, writing-plans) and, if the user asked for manual
   approval, ask the user before generating.
3. Repeat: `round --next` (stop if exhausted) → generate/fix (skipped in a
   review-first flow until a FAIL) → `lint --project` gate → reviewer subagent
   with `prompt reviewer` → `verdict` → PASS ends, FAIL feeds findings back.
4. On exhaustion, report the review file, mirroring the CLI's error.

`meow-cr`/`gitlab-review` are single-shot reviewer dispatches (no loop).
`meow-issue` = MCP fetch → `prepare --branch` → loop → `push`.

### Lint replacement

The SDK PostToolUse hook has no direct equivalent in a shared session. Native
baseline: generator instructions require `meow native lint --file <path>` after
every Write/Edit, and `lint --project` is a hard gate before each review. During
implementation I will verify whether Claude Code skill- or agent-scoped
PostToolUse hooks work for plugin skills and subagents; if so, add them on top,
never instead of, the instruction baseline.

### Skill layout

`skills/_shared/` holds the protocol pieces (prepare, review loop, lint
discipline, resume and error rules). Each of the eight SKILL.md files stays
short: mode selection, its parameters, which shared pieces it applies, and its
CLI-mode fallback. `lint-fix` moves to `meow native lint --project`.

## Testing

- Prompt builders: golden tests proving CLI-path prompts are unchanged.
- Each `meow native` command: temp-repo tests (worktree creation, dirty tree,
  latest-plan exclusions, verdict parsing, lint fix/failure output, round
  counter and exhaustion).
- Skill structure test: frontmatter valid; every `meow native <cmd>` cited in
  a SKILL.md or shared doc exists in the argparse tree; each skill keeps a CLI
  fallback section with the original command.
- Existing suites must pass untouched; `ruff check` clean.

## Docs

README (skills table and native vs CLI section), GUIDE.md, AGENTS.md, and a
plan under `docs/superpowers/plans/`.

## Risks

- Prose-driven loops are less enforceable than Python: mitigated by
  disk-persisted round counter and by making tool output (not memory) decide
  stop conditions.
- Session context growth over many rounds: mitigated by subagent reviewer and
  explorer; generator fixes stay scoped to reviewer findings.
- Hook feasibility unknown: instruction baseline works without it.
- `gh` is not installed on this machine: delivery is push plus compare URL.

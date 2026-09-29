# Native Skill Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Let every meow skill run its plan/generate/review loop inside the calling Claude Code session, while the `meow` CLI/SDK path stays byte-for-byte unchanged.

**Architecture:** Extract role prompts into pure functions (`prompts.py`) shared by the SDK agents and a new agent-free `meow native` helper. Skills drive the loop with Task subagents and superpowers skills, calling `meow native` only for deterministic facts (config, worktree, lint, verdict, round counter, prompts).

**Tech Stack:** Python 3.12+, argparse, unittest, ruff; Markdown skills.

**Spec:** `docs/superpowers/specs/2026-09-29-native-skill-execution-design.md`

## Global Constraints

- CLI behavior, flags, and prompt text unchanged (golden tests).
- `meow native` never starts the Agent SDK; JSON on stdout, logs on stderr.
- ruff limits: max-args 4, max-statements 20, max-branches 7, complexity 6.
- Artifact names/format identical to CLI mode (`<name>.md`, `<stem>-review.md`, `review.md`, `gitlab-review.md`, `SUMMARY:`/`STATUS:`).
- Round-state file is `<plan-stem>.native-state.json` (never `.md`).
- No author/name metadata added anywhere.

## Review Focus

- Dirty main tree or existing worktree on `prepare` behaves like the CLI (error / reuse).
- `round --next` on missing/corrupt state file starts at 1 rather than crashing.
- `verdict` on a file without `STATUS:` returns FAIL.
- `prompt reviewer` for a plan that does not exist errors clearly.
- Lint file path outside project or nonexistent reports a failure, not a crash.

---

### Task 1: Extract prompts into `prompts.py`

**Files:** Create `src/meow/prompts.py`, `tests/test_prompts.py`; modify `agents/{planner,generator,explorer,reviewer,review_fixer,lint_fixer}.py`.

**Produces:** pure functions `planner_prompt(plan_file)`, `generator_prompt(plan_file)`, `explorer_prompt(active_dir)`, `plan_review_prompt(plan_file, focus, lint_commands, check_worktree_hygiene)`, `prompt_review_prompt(...)`, `mr_review_prompt(...)`, `review_fixer_prompt()`, `lint_fixer_prompt()`, plus moved helpers `lint_instructions`, `verification_instructions`, `architecture_review_instructions`, `no_prompt_review_instructions`.

- [ ] Before moving anything, write golden tests capturing the CURRENT strings produced through the existing agents/helpers (run them green).
- [ ] Move builders; agents call them; keep `_`-prefixed names in `reviewer.py` as re-exports (tests import them).
- [ ] Full suite green; commit.

### Task 2: `native.py` deterministic helpers

**Files:** Create `src/meow/native.py`, `tests/test_native.py`.

**Produces:** `prepare(working_dir, name, no_worktree, source_branch, branch) -> dict`, `latest_plan(working_dir)`, `latest_review(working_dir)`, `verdict(path) -> dict`, `lint(working_dir, file|None) -> dict`, `next_round(plan_file, max_rounds) -> dict`, `role_prompt(working_dir, role, plan, focus) -> str`, `push(working_dir, branch)`.

- [ ] Tests first for each (temp git repos for prepare, round counter incl. corrupt file, verdict default FAIL, lint with a fake command).
- [ ] Implement by composing existing functions (`load_config`, `_ensure_clean_tree`, `_boot_repo`, `_resolve_working_dir`, `_ensure_branch_worktree`, `_push_branch`, `_latest_plan_file`, `_latest_review_file`, `_detect_review_flavor`, `_verdict_status`, `load_rules`, `check_lint_commands`, `_run_lint_on_file`).
- [ ] Commit.

### Task 3: `meow native` CLI group

**Files:** Create `src/meow/native_cli.py`; modify `src/meow/cli.py` (register + early dispatch, skipping clean-tree/boot); tests in `tests/test_native_cli.py`.

- [ ] Subcommands: `prepare`, `latest-plan`, `latest-review`, `verdict`, `lint`, `round`, `prompt`, `push`; all accept `--working-dir`; JSON to stdout, errors to stderr with exit 1.
- [ ] Existing `tests/test_cli.py` untouched and green; commit.

### Task 4: Shared protocol + eight skills

**Files:** Create `skills/_shared/{prepare,review-loop,lint-discipline,roles}.md`; rewrite `skills/*/SKILL.md` (sprint, meow-plan, meow-review, meow-cr, review-fix-review, meow-issue, gitlab-review, lint-fix) with native default + preserved CLI-mode section; `tests/test_native_skills.py`.

- [ ] Structure test: frontmatter present, every `meow native <cmd>` cited exists in the parser, each skill keeps its original `meow <cmd>` fallback.
- [ ] Commit.

### Task 5: Docs, verification, push

- [ ] Update README (skills table + native vs CLI), GUIDE.md, AGENTS.md.
- [ ] `ruff check`, full unittest suite, end-to-end smoke of `meow native` in a temp repo.
- [ ] `git push -u origin claude/meow-skills-native-execution-f265a3`; report compare URL (`gh` is not installed).

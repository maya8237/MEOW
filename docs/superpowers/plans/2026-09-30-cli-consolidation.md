# CLI Consolidation: `review`/`cr`/`gitlab-review`/`branch-review`/`review-fix-review`/`issue`/`lint-fix` → `run`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace seven near-duplicate subcommands with one: `meow run`, operating in one of three peer modes — plain build (default, now also Jira-sourced), `--review` (report-only or fix-looping review, sourced from a prompt, `--jira`, `--gitlab`, `--branch`+`--target`, or `--plan-file`/auto-discovery), or `--lint-fix` (project-wide lint fix-or-report, unchanged behavior). `meow plan` is untouched.

**This is a hard removal, not a deprecation.** `review`, `cr`, `gitlab-review`, `branch-review`, `review-fix-review`, `issue`, `lint-fix` stop existing as subcommands — no aliases, no deprecation warnings. Reasoning: meow is pre-1.0 (`0.1.0`), has no documented external-consumer contract for its CLI, and the entire point of this change is fewer endpoints — keeping dead aliases around while adding the new surface doubles exactly the maintenance/test surface the user wants reduced. A clean break is the coherent choice at this version stage.

## Final CLI surface

```
meow run [REQUEST]
    [--name NAME | --no-worktree] [--source-branch BRANCH]
    [--plan-file PATH] [--resume-at {generate,review}] [-m]
    [--jira [KEY]]
    [--review [--fix] [--review-file PATH]
              [--gitlab MR-LINK] [--branch BRANCH --target TARGET]]
    [--lint-fix [--report-only]]
    [--working-dir PATH]

meow plan [REQUEST] --name NAME [--no-worktree] [--source-branch BRANCH]
    (unchanged)
```

`--review` and `--lint-fix` are **peer top-level modes**, exactly like `--jira`-absent vs `--jira`-present already are within build mode: at most one of `--review`/`--lint-fix` may be given; neither given = plain build mode. `--lint-fix` takes no `REQUEST`, no worktree flags (`--name`/`--no-worktree`/`--source-branch`), no `--jira`/`--gitlab`/`--branch`/`--plan-file`/`--fix` — it operates on `--working-dir` in place, exactly like today's `lint-fix`, and any of those given alongside it is an error. Its own `--report-only` is a separate flag from `--review`'s `--fix`, deliberately: `--lint-fix`'s default is to *fix* (mirroring today's `lint-fix` default), `--report-only` turns fixing off; `--review`'s default is to *not* fix, `--fix` turns it on. Reusing one flag name for both would flip one of the two defaults' meaning depending on mode, which is more confusing than one extra flag name.

### Build mode (`--review` not given)

- `REQUEST` positional: required, UNLESS `--jira` is given (then giving both `REQUEST` and `--jira` is an error — ambiguous, two ways to say what to build).
- `--jira` absent: today's exact `run` — plan (unless `--plan-file`) then implement, detached worktree by default (`--name` required unless `--no-worktree`), `--source-branch`/`--resume-at`/`-m` all unchanged.
- `--jira [KEY]` given: fetch the Jira issue (omitted `KEY` = latest in `[jira].project_key`, matching today's `meow issue [ISSUE-KEY]`), use its summary+description as `REQUEST`, build in a **real pushable branch** worktree (`<branch_prefix><KEY>`, auto-derived name — `--name`/`--no-worktree`/`--source-branch` are rejected as meaningless here, matching today's `issue` taking no worktree flags), push to `origin` on success, print `{"issue": KEY, "branch": branch}` JSON on stdout. `-m` still works (same hang-if-scheduled caveat as today). This is exactly today's `meow issue`.

### Review mode (`--review` given)

- `REQUEST` positional becomes the review **prompt/basis** when given as plain text.
- Exactly one **source** may be given: `REQUEST` (prompt text), `--jira KEY`, `--gitlab LINK`, `--branch BRANCH --target TARGET`, or `--plan-file PATH`. Giving more than one is an error.
- **No source given at all**: auto-discover the latest plan file in `docs_dir` (today's `meow review`'s exact default); if none exists, fall back to reviewing the git diff (or whole project if the diff is empty) exactly like today's bare `meow cr`. This composite is deliberate: it's the only way both `review`'s and `cr`'s bare-invocation defaults stay reachable through the same "give me nothing" invocation, rather than making the user remember which of two flags to add for which default. Explicitly passing `--plan-file <real path>` still fails loudly if that file doesn't exist, exactly as today.
- `--fix` (default off): off = single report-only review pass, log PASS/FAIL, never raise (today's `cr`/`gitlab-review` shape — extended to plan/branch sources, which is a **new** report-only mode for them). On = loop review→fix→review to `max_rounds`, raise `RuntimeError` if it never passes (today's `review`/`branch-review`/`review-fix-review` shape).
- `--gitlab LINK` + `--fix` together: **hard error**. There is no local checkout of a merge request to fix — this preserves today's `gitlab-review` being deliberately read-only (and today's `review-fix-review` rejecting a gitlab-flavor review file for the same reason) rather than inventing new scope (checking an MR branch out) nobody asked for.
- `--review-file PATH`: resume fixing an already-written review file instead of running a fresh initial review — today's exact `review-fix-review --review-file` capability. Auto-detects the file's flavor (plan/prompt; gitlab/branch flavors are rejected, same message as today) the same way `review-fix-review` does today, so no other source flag is needed alongside it. Giving `--review-file` implies `--fix` (resuming a review file only makes sense if you're going to act on it — there's no today-equivalent of "resume but don't fix").
- `--branch`/`--target` source: worktree by default (auto-derived name `branch-review-<sanitized-branch>`, exactly today's `branch-review`), `--no-worktree` fixes in place (requires the branch already checked out, same guard as today). `--name` is irrelevant here (ignored) since the worktree name is derived from the branch, not user-supplied.
- `--plan-file PATH` source (or plan auto-discovery): no worktree involved (matches today's `meow review`, which never creates one).

### Lint-fix mode (`--lint-fix` given)

Exactly today's `meow lint-fix`, unchanged behavior, called directly from the new dispatch instead of through its own subcommand:
- Default (no `--report-only`): applies each configured `[[lint]]` command's own `--fix` flag project-wide, then hands whatever's still failing to `LintFixAgent` in a loop (reusing the per-file auto-fix hook) until clean or `max_rounds`. Edits the project in place. Requires a clean working tree (same as today).
- `--report-only`: runs the configured commands and reports; fixes nothing, starts no agent; does not require a clean tree (nothing is edited). This is the mode the (also-renamed, see Task 8) lint-fix skill uses, doing the fixing itself in the calling session.
- `src/meow/lint_fix.py`'s `run_lint_fix(working_dir, report_only)` needs **no changes at all** — only its call site moves, from its own subcommand's dispatch function into `run`'s three-way (now four-way) mode dispatch.

## Mapping table (old → new)

| Old | New |
|---|---|
| `meow run REQUEST --name N [...]` | unchanged |
| `meow plan REQUEST --name N` | unchanged |
| `meow lint-fix` | `meow run --lint-fix` |
| `meow lint-fix --report-only` | `meow run --lint-fix --report-only` |
| `meow review` (bare) | `meow run --review --fix` |
| `meow review --plan-file P` | `meow run --review --fix --plan-file P` |
| `meow cr` (bare) | `meow run --review` |
| `meow cr "prompt"` | `meow run --review "prompt"` |
| `meow gitlab-review LINK` | `meow run --review --gitlab LINK` |
| `meow branch-review B --target T` | `meow run --review --fix --branch B --target T` |
| `meow branch-review B --target T --no-worktree` | `meow run --review --fix --branch B --target T --no-worktree` |
| *(no equivalent — new)* | `meow run --review --branch B --target T` (report-only branch review) |
| `meow review-fix-review "prompt"` | `meow run --review --fix "prompt"` |
| `meow review-fix-review "prompt" --review-file F` | `meow run --review --review-file F` (prompt only needed if `F` turns out to be prompt-flavor and you want a *different* focus than what's in the file already implies — see Task 3 for exact semantics) |
| `meow issue [KEY]` | `meow run --jira [KEY]` |
| `meow issue [KEY] -m` | `meow run --jira [KEY] -m` |
| *(no equivalent — new)* | `meow run --review --jira KEY [--fix]` (review current code against what the ticket asked for) |

Every capability that existed is reachable. Two genuinely new capabilities fall out of making `--fix` uniform: report-only branch/plan review, and Jira-sourced review.

## Architecture

New module `src/meow/review_cli.py` holds one dispatcher, `run_review_command(...)`, replacing the top-level CLI-facing functions in `review_runner.py`, `gitlab_reviewer.py`, `branch_reviewer.py`, `review_fix_review.py` (their private helpers — `_load_gitlab_config`, `_fetch_merge_request`, `_sanitize`, `_ensure_existing_branch_worktree` usage, `_detect_review_flavor`/`_latest_review_file` — stay and get imported, minimizing the diff to already-correct code). `issue_solver.py` is untouched (its `run_issue_solver` becomes `run --jira`'s build-mode implementation verbatim) except for one addition: exposing its Jira-fetch helper for review-mode's `--jira` path to reuse without duplicating the MCP preflight/fetch logic.

`orchestrator.py`'s `_run_rounds`/`_run_review_rounds`/`_run_prompt_fix_rounds` need no changes — they're already source-agnostic. `lint_fix.py`'s `run_lint_fix` needs no changes at all — lint-fix mode is a straight call-site move, not a rearchitecture.

## Review Focus

- `--gitlab ... --fix` must error clearly, not silently ignore `--fix` or silently attempt something unsupported.
- Giving two source flags at once (e.g. `--gitlab` and `--branch`) must error clearly naming both, not silently pick one.
- `--jira` used in build mode together with an explicit `REQUEST` must error (ambiguous), not silently prefer one.
- Report-only mode (`--fix` absent) on a FAIL must log and return normally, never raise — this is a behavior *change* for plan/branch sources (which always looped before) and must not be conflated with the "exhausted max_rounds" raise that `--fix` mode still has.
- Bare `meow run --review` in a project with **no plan and no diff** must produce the same reviewer-graded "whole project" output `cr` gives today, not an error — the auto-discovery fallback chain must not dead-end.
- `--review` and `--lint-fix` given together must error clearly, not silently let one win.
- `--lint-fix` requiring a clean tree only when *not* `--report-only` must carry over exactly (today's `_requires_clean_tree` distinction) — getting this backwards would either block the read-only report path unnecessarily or silently skip the guard on the path that actually edits the project.

---

### Task 1: `review_cli.py` skeleton, source validation, and the prompt/plan-source paths

**Files:**
- Create: `src/meow/review_cli.py`
- Test: `tests/test_review_cli.py` (new)

**Interfaces:**
- Produces: `async def run_review_command(working_dir: Path, prompt: str | None, *, fix: bool, jira_key: str | None, gitlab_link: str | None, branch: str | None, target: str | None, plan_file: Path | None, review_file: Path | None, use_worktree: bool) -> None` (`jira_key`/`gitlab_link`/`branch`: `None` = source not selected; `branch` and `target` are given together or not at all).
- Produces: `_validate_single_source(prompt, jira_key, gitlab_link, branch, plan_file, review_file) -> None`, raising `ValueError` naming the conflicting sources.
- Consumes (this task): `orchestrator._run_review_rounds`, `orchestrator._run_prompt_fix_rounds`, `plan_files._latest_plan_file`, `agents.reviewer.ReviewerAgent`, `agents.base.ProjectContext`, `sprint.build_sprint`, `config.load_config`, `lint.describe_lint_plan`.

- [ ] **Step 1: Write failing tests** for: exactly-one-source validation (two sources → `ValueError` naming both; zero sources with a plan present → uses it; zero sources with no plan → falls back to prompt-review path with `prompt=None`); prompt-source report-only (calls `review_prompt`, doesn't raise on FAIL, no `ReviewFixAgent`); prompt-source `--fix` (calls `_run_prompt_fix_rounds` with a `review_prompt`-based `re_review`, raises on exhausted rounds); plan-source report-only (single `review_plan` call, no `Generator`); plan-source `--fix` (calls `_run_review_rounds`, raises on exhausted rounds, matches today's `run_review`'s exact error text shape).

  Use `tests/test_review_fix.py`'s and `tests/test_gitlab_reviewer.py`'s existing mock-patching style (`patch("meow.review_cli.ReviewerAgent.review_prompt", new=AsyncMock(...))`, `patch("meow.review_cli.load_config", ...)`) as the template — same shape, new module path.

- [ ] **Step 2: Run, confirm failure** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `run_review_command` handling only the prompt-source and plan-source paths (auto-discovery included) for now; `jira_key`/`gitlab_link`/`branch` accepted but raise `NotImplementedError` if set (later tasks fill them in) so this task is independently testable. Report-only path: single reviewer call, log status, return. `--fix` path: reuse `_run_review_rounds` (plan) / `_run_prompt_fix_rounds` with `re_review=lambda: ReviewerAgent(context).review_prompt(prompt)` (prompt), raising the same `RuntimeError` shape `review_runner.py`/`review_fix_review.py` use today.

- [ ] **Step 4: Run tests, confirm pass.**

- [ ] **Step 5: Commit.**

---

### Task 2: `--gitlab` and `--branch` sources

**Files:**
- Modify: `src/meow/review_cli.py`
- Test: `tests/test_review_cli.py` (extend)

**Interfaces:**
- Consumes: `gitlab_reviewer._load_gitlab_config`, `gitlab_reviewer._fetch_merge_request`, `agents.gitlab_fetcher.GitlabFetcherAgent`, `agents.reviewer.ReviewerAgent.review_merge_request`, `worktree._ensure_existing_branch_worktree`, `worktree._require_branch_checked_out`, `agents.reviewer.ReviewerAgent.review_branch`, `branch_reviewer._sanitize`.

- [ ] **Step 1: Write failing tests**: `--gitlab` + `fix=True` raises `ValueError` before fetching anything; `--gitlab` report-only fetches and calls `review_merge_request`, logs status, never raises; `--branch` report-only (worktree and `--no-worktree` variants) calls `review_branch` once, no fix loop; `--branch` + `--fix` loops via `_run_prompt_fix_rounds` with a `review_branch`-based `re_review`, raises on exhaustion — mirror `tests/test_branch_reviewer.py`'s exact fixture shapes (they're being deleted in Task 6; copy the assertions forward here first, deletion happens once this task's equivalents pass).

- [ ] **Step 2: Run, confirm failure** (`NotImplementedError` from Task 1's placeholder).

- [ ] **Step 3: Implement** both source branches in `run_review_command`.

- [ ] **Step 4: Run tests, confirm pass.**

- [ ] **Step 5: Commit.**

---

### Task 3: `--jira` source (review mode) and `--review-file` resume

**Files:**
- Modify: `src/meow/review_cli.py`
- Modify: `src/meow/issue_solver.py` (expose the fetch helper for reuse without duplication)
- Test: `tests/test_review_cli.py` (extend)

**Interfaces:**
- Consumes: `issue_solver._load_jira_config`, `issue_solver._fetch_issue` (already module-private; import directly — same package, no need to re-export).
- Produces: a small prompt-building helper, e.g. `_jira_review_prompt(issue: dict) -> str` — `f"Review whether the current code satisfies Jira issue {issue['key']}: {issue['summary']}\n\n{issue['description']}"`.

- [ ] **Step 1: Write failing tests**: `--jira` fetches the issue then behaves exactly like the prompt-source path with the built text (report-only and `--fix` variants, reusing Task 1's prompt-path assertions against the fetched text); `--review-file` alone (no other source) auto-detects flavor and resumes (plan-flavor → `_run_review_rounds` with `initial_verdict`; prompt-flavor → `_run_prompt_fix_rounds` with `initial_verdict`); `--review-file` pointing at a gitlab/branch-flavor file raises the same rejection message `review_fix_review.py` uses today; `--review-file` implies `fix=True` even if the caller passed `fix=False` (document this — CLI wiring in Task 4 makes it explicit at the argparse level too).

- [ ] **Step 2: Run, confirm failure.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests, confirm pass.**

- [ ] **Step 5: Commit.**

---

### Task 4: CLI wiring — `cli.py`

**Files:**
- Modify: `src/meow/cli.py`
- Test: `tests/test_cli.py` (extensively rewritten — see Task 6)

**Interfaces:**
- Produces: `_add_run_parser` gains `--review`, `--fix`, `--jira` (`nargs="?"`, `const=""`, `default=None`), `--gitlab`, `--branch`, `--target`, `--review-file`, `--lint-fix`, `--report-only`; `request` becomes `nargs="?"`.
- Produces: `_dispatch_feature`'s `run` branch splits four ways: `args.lint_fix` → `run_lint_fix(...)` (today's `_dispatch_lint_fix`, moved here); `args.review` → `run_review_command(...)`; `args.jira is not None` (and neither of the above) → `run_issue_solver(...)` (today's `_dispatch_issue`, moved here); none of the above → today's `run_sprint(...)` unchanged.

- [ ] **Step 1**: Add the new arguments to `_add_run_parser`. Add validation in a new `_validate_run_flags(parser, args)` (called from `cli_main` alongside the existing `_validate_feature_name_requirement`): at most one of `--review`/`--lint-fix`; `request` xor `--jira` in build mode; `--gitlab`+`--fix` together; more than one of {request, `--jira`, `--gitlab`, `--branch`} given in review mode (branch requires target and vice versa, checked here too); `--lint-fix` given alongside `request`/`--jira`/worktree flags/`--review`-only flags is an error naming what was misused; `--report-only` given without `--lint-fix` is an error.
- [ ] **Step 2**: Scope `_validate_feature_name_requirement`/`_should_use_worktree` so `--name` is required only for plain build-mode `run` (not `--review`, not `--jira`, not `--lint-fix` — none of the three create a user-named worktree).
- [ ] **Step 3**: Rewrite `_dispatch_feature`'s `run` branch as described above; keep `plan`'s branch untouched.
- [ ] **Step 4**: Remove `_add_review_parser`, `_add_cr_parser`, `_add_gitlab_review_parser`, `_add_branch_review_parser`, `_add_review_fix_review_parser`, `_add_issue_parser`, `_add_lint_fix_parser`, their `_dispatch_*` functions, and their `_COMMAND_HANDLERS` entries. Remove the now-dead imports (`run_review`, `run_prompt_review`, `run_gitlab_review`, `run_branch_review`, `run_review_fix_review`; keep `run_issue_solver` and `run_lint_fix`, both now called from `_dispatch_feature`). Update `_requires_clean_tree`/`_boot_repo`'s `include_gitignore` condition: replace the `args.command == "issue"`/`"branch-review"`/`"lint-fix"` special cases with checks against `args.command == "run"` plus the relevant flag (`args.jira is not None`, or `args.review and args.branch`, for `include_gitignore`; `args.lint_fix and not args.report_only`, or plain build mode, for `_requires_clean_tree` — `--review`'s other sources and `--lint-fix --report-only` never require a clean tree, exactly like today's `cr`/`review`/`gitlab-review`/`review-fix-review`/`lint-fix --report-only`).
- [ ] **Step 5**: Run `python -m unittest tests.test_cli` (expect large-scale failures — Task 6 rewrites this file); run `ruff check src/meow/cli.py` alone to catch syntax/import issues early.
- [ ] **Step 6**: Commit is deferred to the end of Task 6 (test file must move in lockstep with this one, or the tree is left in a broken, half-migrated state — see Task 6).

---

### Task 5: Delete the superseded top-level flow functions (`lint_fix.py` is NOT one of these — it stays, unchanged, just called from a new dispatch site)

**Files:**
- Modify: `src/meow/review_runner.py` (delete `run_review`, `run_prompt_review`; delete file entirely if nothing else references it — check first)
- Modify: `src/meow/gitlab_reviewer.py` (delete `run_gitlab_review`; keep `_load_gitlab_config`, `_fetch_merge_request`)
- Modify: `src/meow/branch_reviewer.py` (delete `run_branch_review`; keep `_sanitize`)
- Modify: `src/meow/review_fix_review.py` (delete `run_review_fix_review`; check whether anything else needs it — if not, delete file entirely)

- [ ] **Step 1**: For each file, `grep -rn` the function name across `src/` and `skills/` to confirm only `cli.py` (already updated in Task 4) referenced it.
- [ ] **Step 2**: Delete the dead function (or the whole file, if nothing survives — `review_runner.py` and `review_fix_review.py` are likely candidates for full deletion; `gitlab_reviewer.py` and `branch_reviewer.py` keep their still-used helpers).
- [ ] **Step 3**: `ruff check .` project-wide to catch now-unused imports.
- [ ] **Step 4**: Commit alongside Task 4 and Task 6 (see Task 6's final step — these three land together since the tree doesn't type-check/import cleanly split across them).

---

### Task 6: Rewrite `tests/test_cli.py`; delete the superseded test files

**Files:**
- Modify: `tests/test_cli.py` (large rewrite)
- Delete: `tests/test_gitlab_reviewer.py`, `tests/test_branch_reviewer.py`, `tests/test_review_fix.py` (their `_detect_review_flavor`/`_latest_review_file` tests move to `tests/test_review_cli.py` or `tests/test_plan_files.py` if not already covered there — check `plan_files.py` has no existing dedicated test file first)
- Modify: `tests/test_review_cli.py` (fold in the flavor-detection tests if relocated here)

**Interfaces:**
- Consumes: everything from Tasks 1–3's `review_cli.py`.

- [ ] **Step 1**: Rewrite `test_cli.py`'s argparse-level tests for `run`: request-xor-jira validation, gitlab+fix validation, multi-source validation, `--name` not required under `--review`/`--jira`.
- [ ] **Step 2**: Rewrite `test_cli.py`'s dispatch-level tests (the `patch("meow.cli.X", new_callable=AsyncMock)` style) for the four-way `run` split: plain build → `run_sprint`; `--jira` → `run_issue_solver` (port the existing `test_issue_command_*` tests here, changed from `issue` to `run --jira`); `--review` (each source) → `run_review_command` with the right kwargs — port the intent of `test_gitlab_review_command_is_supported`, `test_branch_review_command_is_supported` (both classes), `test_review_fix_review_command_is_supported` here, checking the `run_review_command` call's kwargs instead of five different functions' call signatures; `--lint-fix` → `run_lint_fix` (port `test_lint_fix_command_is_supported`, `test_lint_fix_command_requires_clean_tree_by_default`, `test_lint_fix_report_only_skips_the_clean_tree_check`, changed from `lint-fix` to `run --lint-fix`).
- [ ] **Step 3**: Delete `tests/test_gitlab_reviewer.py`, `tests/test_branch_reviewer.py`, `tests/test_review_fix.py` after confirming every assertion they made has a live equivalent in `test_review_cli.py`/`test_cli.py` (this is the point of Task 6 — no coverage may be lost, only relocated).
- [ ] **Step 4**: `python -m unittest discover -s tests -v` — full suite. This is the first point where Tasks 4/5/6 can be verified together; expect to bounce between them fixing import errors and mismatched call signatures.
- [ ] **Step 5**: `ruff check .` project-wide.
- [ ] **Step 6**: Commit Tasks 4, 5, and 6 together (`git add -A` scoped to the touched paths) — this is the one point in the plan where three tasks land in a single commit, because `cli.py`, the deleted flow-function files, and the test suite are mutually load-bearing and the tree doesn't pass tests in any partial combination of the three.

---

### Task 7: Native mode — `native_prepare.py`, `native_prompt.py`, `native_cli.py`

**Files:**
- Modify: `src/meow/native_prepare.py`, `src/meow/native_prompt.py`, `src/meow/native_cli.py`
- Test: `tests/test_native.py`, `tests/test_native_cli.py` (extend)

Native mode's `reviewer-plan`/`reviewer-prompt`/`reviewer-mr`/`reviewer-branch` roles and `prepare --branch`/`--existing-branch` already map cleanly onto the new sources — **no new prompt roles are needed**, since native mode was already source-parameterized by the branch-review work. What changes is purely the *skill-level* dispatch table (Task 8) choosing which `meow native prompt <role>` to call based on the same `--jira`/`--gitlab`/`--branch`/`--plan-file` selection logic now living in `review_cli.py`. Confirm this by re-reading `skills/_shared/native-mode.md`'s review-loop section against the new source list; if a genuine gap exists (e.g. no native prompt role read Jira text as a review basis), add the smallest addition that closes it — a `reviewer-jira` role is likely unnecessary since `--jira`'s review-mode behavior is "fetch text, treat as `reviewer-prompt`'s focus," reusing the existing `reviewer-prompt` role with the fetched text as `--focus`.

- [ ] **Step 1**: Read `skills/_shared/native-mode.md` and every skill under `skills/` end to end (needed regardless, for Task 8) before deciding whether any native primitive is actually missing.
- [ ] **Step 2**: If a gap is found, write the failing test, implement, verify — same TDD shape as every prior native-mode task this session.
- [ ] **Step 3**: If no gap is found (likely, given the above), state that finding plainly instead of adding speculative code, and move to Task 8.

---

### Task 8: Skills — consolidate to match the new CLI surface

**Files:**
- Delete: `skills/meow-review/`, `skills/meow-cr/`, `skills/gitlab-review/`, `skills/branch-review/`, `skills/review-fix-review/`, `skills/meow-issue/`
- Modify: `skills/sprint/SKILL.md` (becomes the one skill covering build, review, and lint-fix modes of `run`, or is split by mode — **decide during this task** based on how large a single skill file would get; a single skill with clear "build mode" / "review mode" / "lint-fix mode" sections is likely cleaner than shipping this much branching logic split across artificial file boundaries, but re-evaluate once the shared native-mode protocol section is actually drafted). `skills/lint-fix/SKILL.md` is included in this consolidation too (folds into the same skill(s) as the other five) — it wasn't listed in the original delete line above because the original scope predates the `--lint-fix` addition; delete it alongside the other five.
- Modify: `tests/test_native_skills.py`'s `CLI_FALLBACKS`

- [ ] **Step 1**: Draft the consolidated skill(s)' native-mode section, covering every source (prompt/jira/gitlab/branch/plan-file/auto-discovery) and both fix/report-only shapes, reusing existing `meow native prepare`/`prompt`/`round`/`lint`/`verdict` calls per Task 7's conclusion.
- [ ] **Step 2**: Draft the CLI-mode section: the new `meow run [--review] [...]` invocations, one example per source.
- [ ] **Step 3**: Update `CLI_FALLBACKS` to match whatever skill directory layout Step 1 settled on; delete the six superseded skill directories.
- [ ] **Step 4**: `python -m unittest tests.test_native_skills -v` — all structural checks pass against the new layout.

---

### Task 9: Docs — README, AGENTS, GUIDE, INTEGRATIONS

**Files:**
- Modify: `README.md`, `AGENTS.md`, `GUIDE.md`, `docs/INTEGRATIONS.md`

- [ ] **Step 1**: Replace README's `### meow review`/`### meow cr`/`### meow gitlab-review`/`### meow branch-review`/`### meow review-fix-review`/`### meow lint-fix` sections and the `meow issue` section with one `### meow run` section (build mode), one `### meow run --review` section (review mode, covering every source with an example), and one `### meow run --lint-fix` section (lint-fix mode, noting it's behavior-identical to today's standalone `lint-fix`), plus the mapping table from this plan's header condensed into prose, and an updated plugin-skills table reflecting Task 8's final skill layout. Update the `Layout` file tree (`review_cli.py` replaces the four deleted/trimmed modules; `lint_fix.py` stays listed, unchanged; the deleted skill directories are removed from the tree).
- [ ] **Step 2**: Update AGENTS.md's command-reference paragraphs the same way.
- [ ] **Step 3**: Update GUIDE.md's §7 troubleshooting line (command names changed) and any other command-name references.
- [ ] **Step 4**: Update `docs/INTEGRATIONS.md`'s Jira/GitLab sections wherever they reference `meow issue`/`meow gitlab-review` by name.
- [ ] **Step 5**: Re-read all four files end to end for stale references (`grep -rn "meow review\|meow cr \|gitlab-review\|branch-review\|review-fix-review\|meow issue"` across `*.md` at the repo root and `docs/`) and fix anything Steps 1–4 missed.

---

### Task 10: Full verification and push

- [ ] **Step 1**: `python -m unittest discover -s tests -v` — full suite, record the new pass count.
- [ ] **Step 2**: `ruff check .` project-wide.
- [ ] **Step 3**: Manual sanity check: `meow run --help`, confirm every new flag is listed; try a couple of the mapping table's new invocations against a scratch `.harness.toml` project if one is cheaply available (reuse a temp fixture like the test suite's `make_repo`, run the actual CLI end to end with mocked SDK calls out of reach — if full end-to-end isn't practical without live Claude Code, `--help` plus the test suite's coverage is the achievable verification and should be stated as such, not oversold).
- [ ] **Step 4**: `git fetch origin && git rev-list --left-right --count main...origin/main` — confirm still `0 0` before pushing.
- [ ] **Step 5**: Push. Report the final commit hash(es) and the final CLI surface.

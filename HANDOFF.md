# Handoff: remove the remaining leftovers, no backwards compatibility

Context: `main` at `4b0c845` already dropped `builtin.knowledge`, `builtin.shape`,
`project/preplan.py`, `project/shaping.py`, the `needs_user_decision` pause, the
`native shape-*` / `knowledge-*` commands and `integrations/knowledge*`.
The items below are what was deliberately left. Out of scope: `builtin.verify`
(keep it). Remove everything below outright: no shims, aliases, deprecation
notes, "legacy" fallbacks, or tolerance for old journals/config.

Verify with `python -m pytest -q` (about 2 min; run in the background),
`ruff check .`, `ruff format --check .`. Keep docs/skills under 200 lines.
Update `docs/` and `skills/` in the same change.

## 1. Run-resume `--request` plumbing (dead for ordinary runs)

`meow resume --request` now only works for a blocked orchestrator task
(`execution/orchestration/runner.py`, `_reset_task`/`_unblock`). For plain runs
it only returns an error. Keep the orchestrator behaviour; delete the rest:

- `cli/resume_cli.py`: `_request_problem` and its two call sites; reject the
  flag for non-orchestrator runs at the single place that dispatches to the
  orchestrator instead.
- `infrastructure/ui_sessions.py`: `request` parameter and `--request` argv
  for MEOW run attempts in `_validate_imported_resume`, `_imported_resume_recipe`,
  `_imported_resume_argv`, `resume_imported_run`, `resume_argv` (run branch),
  `resume_attempt`; and `_resume_supported`'s `_request_problem` call.
  The Claude-session branch of `resume_argv` may keep `request` (it is the
  prompt override); confirm before touching it.
- `ui/session_api.py` (~lines 406-430 and 461-470): drop `request` from the
  run resume payloads.
- Tests: `tests/ui/test_session_runs.py`, `tests/ui/test_session_api.py`.
- Docs: `docs/CLI.md` line 83 should only show `--task T --request` for
  orchestrators.

## 2. `needs_decision` UI attempt state

Nothing in the run path produces it any more. Remaining producers are the
Claude-session `error_max_turns` branch (`ui_sessions.py` ~674) and the
state carry-over (~790). Decide with the owner whether that state is still
wanted; if not, remove it everywhere:

- `ui_sessions.py` (lines ~242, 570, 674, 790), `ui/sessions.py` (~659).
- `frontend/src/components/SessionsView.tsx`, `SessionDetail.tsx`,
  `SessionsView.test.tsx`, `frontend/src/styles.css` (`.state-needs_decision`
  at ~756, ~986, ~1495).
- Rebuild the bundle into `src/meow/ui/static/` (do not hand-edit
  `index-*.js` / `index-*.css`; check `frontend/package.json` for the build
  script and commit the regenerated assets).
- Tests referencing it under `tests/ui/`.

## 3. Stale preplan/legacy residue

- `execution/run_state.py:318` docstring mentions `<id>.preplan.json`; the
  sidecar no longer exists. Keep the `_ID` glob filter only if other sidecars
  exist; otherwise simplify.
- `docs/WORKFLOWS.md:193` "Legacy journals select default regardless of current
  config" and `project/plan_files.py:11` "Legacy fixed review names; still
  recognised so older review files resume" (+ `docs/CLI.md:117`): remove the
  legacy name recognition and the doc sentences, then update tests.
- `project/migration/` (`.harness.toml` to `.meow/config.toml` conversion,
  called from `project/onboarding.py:271`) and the `migration` skill
  (`docs/SKILLS.md:83`): this is explicit legacy support. Remove the package,
  the onboarding call, the skill, its tests, and mentions in `AGENTS.md`
  (the "migration converts legacy layouts" line).
- `infrastructure/worktree_names.py:27` (legacy branch renaming),
  `infrastructure/review_verification.py:227` (legacy report dirs),
  `project/claude_settings.py:195`, `native/native_models.py:14`
  ("omitted host preserves legacy Claude dispatch"), `execution/delivery.py:88`,
  `docs/CLEANUP.md:17` (legacy `meow/<run-id>` branches): read each, delete the
  legacy branch of logic, keep only the current behaviour.
- `docs/ARCHITECTURE.md:5` already forbids compatibility modules; grep for
  `legacy`, `backward`, `compat` after the above to catch anything missed.

## Order and checks

1. Item 1, run tests. 2. Item 3 (one commit per bullet group). 3. Item 2
   last, since it needs a frontend rebuild.
2. After each step: pytest, ruff check, ruff format --check.
3. Push to `main` only if the owner repeats the instruction for this handoff.

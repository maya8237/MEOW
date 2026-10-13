# Task 3 implementation report

## What changed

- Added optional, validated UI session and attempt IDs to run records. `RunStore.create` consumes worker environment context only when all three values are present and the resolved repository matches; old records still load without the new fields.
- Added imported-run serialization, run output paging from the fixed `<runs>/<run-id>.log` path, resume/cancel eligibility, cooperative cancellation, and detached UI-owned resume attempts linked to the existing run ID.
- Kept resume commands server-owned and reconstructed session recipe facts from the saved request and workflow record. Historical argv is not inferred.
- Forwarded the saved unattended delivery policy through `resume_cli.resume()`.
- Updated the worker to preserve an imported attempt's pre-seeded run ID for cancellation and completion tracking.

## TDD evidence

RED was run against a clean archive of base commit `51171e0` with the corrected tests copied into it. The clean-base command was `rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe .tdd_red_check.py'`; the pytest command executed by that temporary harness was:

```text
C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=<clean-base>/.pytest-tmp tests/ui/test_session_runs.py tests/cli/test_resume_workflow.py::test_resume_propagates_saved_unattended_policy -q
```

Expected RED output:

```text
FAILED tests/ui/test_session_runs.py::test_run_store_persists_explicit_worker_ui_session_association
FAILED tests/ui/test_session_runs.py::test_run_store_rejects_invalid_explicit_ui_ids[ui_session_id-../runs]
FAILED tests/ui/test_session_runs.py::test_run_store_rejects_invalid_explicit_ui_ids[ui_attempt_id-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA]
FAILED tests/ui/test_session_runs.py::test_run_store_rejects_partial_or_mismatched_worker_context[context0]
FAILED tests/ui/test_session_runs.py::test_run_store_rejects_partial_or_mismatched_worker_context[context1]
FAILED tests/ui/test_session_runs.py::test_unassociated_legacy_run_loads_without_ui_fields
FAILED tests/ui/test_session_runs.py::test_imported_runs_include_phase_output_availability_and_session_link
FAILED tests/ui/test_session_runs.py::test_imported_run_without_history_reports_no_output_and_no_cancel
FAILED tests/ui/test_session_runs.py::test_imported_run_actions_follow_current_worker_inspection
FAILED tests/ui/test_session_runs.py::test_run_output_uses_fixed_log_and_bounded_cursor
FAILED tests/ui/test_session_runs.py::test_imported_run_resume_creates_detached_attempt_linked_to_existing_run
FAILED tests/ui/test_session_runs.py::test_cancel_imported_run_requests_cooperative_cancel
FAILED tests/cli/test_resume_workflow.py::test_resume_propagates_saved_unattended_policy
13 failed in 3.39s
baseline_exit_code=1
```

These failures showed the missing association fields/validation, imported-run helpers, output reader, detached resume/cancel operations, and unattended propagation.

GREEN command:

```text
$env:PYTHONPATH='src'; rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.pytest-tmp tests/ui/test_session_runs.py tests/cli/test_resume_workflow.py -q'
```

GREEN output:

```text
.....................                                                    [100%]
21 passed in 6.65s
```

## Worker output and storage race follow-up

- Added a CLI-only chunk reader so flushed text without a newline reaches the session log before the child exits. Claude Code JSON output retains newline framing. The deterministic child test prints `partial` without a newline, stays alive for six seconds, and verifies the text is visible while the attempt is still running; cleanup requests stop and reaps the child.
- During the focused lifecycle suite, the stop test once recorded `Worker failed: [WinError 5] Access is denied` at `os.replace(temp, <pytest tmp repo>/.meow/ui/sessions/<session-id>/<attempt-id>.json)`. The worker stores exception text rather than traceback. This test uses an isolated `tmp_path` repository, not the MEOW checkout.
- Root cause: `update_attempt` performed an unlocked read-modify-replace while the worker appended output and updated `output_size`, the API stop call changed state, and `read_output` polling updated worker output metadata. Those simultaneous writers could replace the same attempt JSON and lose disjoint fields. A second race existed between `update_recipe` and `create_attempt`, which could lose the newly appended attempt list. Unlocked readers could also hold attempt JSON open during replacement on Windows.
- Added bounded, per-session and per-attempt OS advisory transaction locks. Windows uses `msvcrt.locking`; POSIX uses `flock`. Kernel ownership releases locks when a process exits, so recovery does not depend on lock age or a PID-only check. Attempt reads, updates, and output polling metadata use the attempt lock. Session index reads and read-modify-write operations use the session lock. When both are required, order is session then attempt; `update_attempt` releases the attempt lock before syncing session state and then re-reads the current attempt under that order. The future recipe remains editable while an attempt is active.
- Added deterministic barrier tests for concurrent disjoint attempt field updates and an edit racing with attempt creation. Existing lifecycle coverage exercises output polling during stop.
- Recipe edits preserve an active attempt's session state; edits after the attempt is terminal return the recipe to draft. The active-edit test asserts the attempt remains `running`.

## Review fix: stale completion state

A deterministic regression pauses the previous attempt's terminal-state sync after its attempt JSON has been updated but before it acquires the session lock. It then creates the next attempt and lets the old sync continue.

RED command and output:

```text
$env:PYTHONPATH='src'; rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m pytest -q tests/infrastructure/test_ui_sessions.py::test_finishing_previous_attempt_cannot_overwrite_next_starting_state'
F                                                                        [100%]
E       AssertionError: assert 'succeeded' == 'starting'
1 failed in 1.62s
```

The session sync now derives lifecycle state from the latest attempt in the session index while holding the session lock and then the latest attempt lock. A delayed completion for an older attempt can no longer overwrite a newer attempt's `starting` state.

GREEN command and output:

```text
$env:PYTHONPATH='src'; rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m pytest -q tests/infrastructure/test_ui_sessions.py tests/ui/test_session_runs.py tests/cli/test_resume_workflow.py'
.......................................... [100%]
42 passed in 19.23s
```

Ruff check passed, format check passed (`7 files already formatted`), and `rtk git diff --check` was clean.

Follow-up focused command and output:

```text
$env:PYTHONPATH='src'; rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m pytest -q tests/infrastructure/test_ui_sessions.py tests/ui/test_session_runs.py tests/cli/test_resume_workflow.py'
......................................... [100%]
41 passed in 19.56s
```

Ruff and whitespace checks:

```text
rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m ruff check src/meow/ui/sessions.py src/meow/execution/run_state.py src/meow/infrastructure/ui_sessions.py src/meow/cli/resume_cli.py tests/infrastructure/test_ui_sessions.py tests/ui/test_session_runs.py tests/cli/test_resume_workflow.py'
All checks passed!

rtk run 'C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe -m ruff format --check src/meow/ui/sessions.py src/meow/execution/run_state.py src/meow/infrastructure/ui_sessions.py src/meow/cli/resume_cli.py tests/infrastructure/test_ui_sessions.py tests/ui/test_session_runs.py tests/cli/test_resume_workflow.py'
7 files already formatted

rtk git diff --check
```

## Self-review

- Confirmed the imported-run serializer emits only the Task 4 shared schema and reports missing historical output through `background_output_available: false`.
- Confirmed run output reads a server-selected log path after validating the run ID; it ignores the journal's `background.log` value and blocks symlink output paths.
- Confirmed imported resume creates an attempt before launching the existing detached worker and stores the existing run ID on that attempt. The test replaces only the worker launch boundary, so no agents run.
- Confirmed stop handling includes the seeded run ID, so imported resumes can request cooperative cancellation.
- Confirmed CLI partial output appears while its child is still alive, and stop returns with the child tree reaped.
- Confirmed attempt and session metadata read-modify-write paths share bounded OS locks; deterministic overlapping updates preserve both changes and attempt history.
- Confirmed the targeted tests and Ruff checks pass. No paid launches are performed by the tests.

## Files changed

- `src/meow/execution/run_state.py`
- `src/meow/ui/sessions.py`
- `src/meow/infrastructure/ui_sessions.py`
- `src/meow/cli/resume_cli.py`
- `tests/infrastructure/test_ui_sessions.py`
- `tests/ui/test_session_runs.py`
- `tests/cli/test_resume_workflow.py`
- `.superpowers/sdd/2026-10-13-ui-sessions/task-3-report.md`

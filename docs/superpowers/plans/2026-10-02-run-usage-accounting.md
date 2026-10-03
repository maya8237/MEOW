# Run Usage Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record reliable SDK turns, token use, and cost for feature runs and show them in `meow status`.

**Architecture:** Normalize each SDK `ResultMessage` into a small, additive usage entry. Attach entries to the existing atomic `RunStore` journal through the active run context, then render totals and per-role detail from that record. Keep unknown fields unavailable; never infer zero usage from missing SDK data.

**Tech Stack:** Python 3.11+, Claude Agent SDK `ResultMessage`, existing `RunStore`, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), especially “Checkpoints, status, and accounting.” This is the first independently testable slice of the run-control foundation; permission policy, cancellation, and worktree cleanup receive separate plans.

## Global Constraints

- `--unattended` must not prompt at any phase.
- Missing SDK usage must be labeled `unavailable`, never zero.
- Existing checkpoint files must remain readable.
- Do not change agent role tool lists or permission behavior in this slice.
- Preserve pre-existing local edits in `src/meow/delivery/core.py` and `src/meow/native/__init__.py` if still present at execution time.
- Feature work in this repository follows `AGENTS.md`; execute the implementation through the MEOW harness or an explicitly approved equivalent isolated workflow, and inspect worktree state before editing.

## Review Focus

1. An SDK result with `usage=None` leaves usage unavailable rather than reporting zero tokens.
2. A persistent generator emits multiple results; accounting adds each turn without replacing earlier entries.
3. A retry after a CLI crash does not count a result that was never received.
4. A corrupt or older checkpoint without usage entries remains readable by `meow status`.
5. A token/cost value outside the expected SDK shape is ignored safely rather than crashing a run.

---

## File map

- `src/meow/usage.py`: pure SDK-result normalization and aggregation.
- `src/meow/run_state/core.py`: additive usage-entry persistence in the existing atomic journal.
- `src/meow/agents/base.py`: shared SDK-result capture for normal one-shot and persistent streams.
- `src/meow/agents/reviewer.py`: capture results from its custom stream.
- `src/meow/sprint_runner/core.py`: activate the feature run's usage scope after the journal exists, and reset it when the run exits.
- `src/meow/status_cli/core.py`: concise totals and verbose per-role entries.
- `tests/execution/test_run_state.py`, `tests/agents/test_agents.py`, `tests/cli/test_status_cli.py`: behavior at the journal, stream, and CLI boundaries.
- `docs/CLI.md`: document the new status fields and `unavailable` semantics.

### Task 1: Normalize and persist SDK usage

**Files:** Create `src/meow/usage.py`; modify `src/meow/run_state/core.py`; test `tests/execution/test_run_state.py` and `tests/test_usage.py`.

**Interfaces:**

- `usage_entry(role: str, result: object) -> dict[str, object]` returns a JSON-safe entry with `role`, `turns`, `duration_ms`, `tokens` (integer or `None`), and `cost_usd` (finite float or `None`). It retains the SDK's raw token breakdown only when it is a dict of finite nonnegative numbers. The stream layer calls it only for SDK `ResultMessage` objects.
- `RunStore.add_usage(run_id: str, entry: dict[str, object]) -> RunRecord` appends one entry under `record.usage["entries"]` using the existing atomic write; it does not alter `phase` or transition history.
- `usage_totals(value: object) -> dict[str, object]` sums known values and returns `None` for a metric when no entry has it.

- [ ] **Step 1: Write failing tests.** Add tests with a `SimpleNamespace` exposing the SDK result fields. Assert that two entries produce additive totals, `None` remains unknown, malformed numeric values are ignored, and an old record with `usage="unavailable"` can accept its first entry. Verify `add_usage` leaves the phase and transition count unchanged.

  ```python
  result = SimpleNamespace(num_turns=2, duration_ms=1200,
                           total_cost_usd=0.25,
                           usage={"input_tokens": 10, "output_tokens": 4})
  assert usage_entry("generator", result)["tokens"] == 14
  assert usage_totals({"entries": [usage_entry("generator", result)]})["cost_usd"] == 0.25
  ```
- [ ] **Step 2: Confirm red.** Run `rtk pytest tests/test_usage.py tests/execution/test_run_state.py -q`; expect the new interface tests to fail because `meow.usage` and `RunStore.add_usage` do not exist.
- [ ] **Step 3: Implement the pure normalizer and journal method.** Read `ResultMessage.num_turns`, `duration_ms`, `total_cost_usd`, and `usage` defensively. Define tokens as the sum of available input and output token counters; preserve detailed counters separately. Reject NaN, infinity, booleans, and negative figures. Use `RunStore.load`, update `record.usage`, and call its existing `_write`; do not add a phase transition for a usage event.

  ```python
  def add_usage(self, run_id: str, entry: dict[str, object]) -> RunRecord:
      record = self.load(run_id)
      previous = record.usage if isinstance(record.usage, dict) else {}
      record.usage = {"entries": [*previous.get("entries", []), entry]}
      record.updated_at = _now()
      self._write(record)
      return self.load(run_id)
  ```
- [ ] **Step 4: Confirm green.** Run `rtk pytest tests/test_usage.py tests/execution/test_run_state.py -q` and `rtk ruff check src/meow/usage.py src/meow/run_state/core.py tests/test_usage.py tests/execution/test_run_state.py`.
- [ ] **Step 5: Commit this independently reviewable slice.** Stage only this task's files and commit with `feat: persist SDK usage in run journal`.

### Task 2: Capture SDK results from all feature-run streams

**Files:** Modify `src/meow/usage.py`, `src/meow/agents/base.py`, `src/meow/agents/reviewer.py`, and `src/meow/sprint_runner/core.py`; test `tests/agents/test_agents.py` and `tests/execution/test_run_journal.py`.

**Interfaces:**

- `usage_scope(store: RunStore, run_id: str)` is a context manager backed by `ContextVar`; it restores the prior value on exit.
- `record_result(role: str, message: object) -> None` returns immediately unless `message` is an SDK `ResultMessage` and a usage scope exists. It calls `RunStore.add_usage` exactly once per received result.
- `log_stream_message` invokes `record_result` for a result while retaining its current logging behavior. The reviewer's custom stream invokes `record_result` directly for its result branch.

- [ ] **Step 1: Write failing stream tests.** Within `usage_scope(store, record.id)`, feed a successful `ResultMessage` through `log_stream_message`, then two more results as a persistent generator would; assert three entries with their roles. Feed a non-result message and assert no entry. Cover the reviewer's custom stream separately. After scope exit, assert another result does not mutate the record.

  ```python
  with usage_scope(store, record.id):
      log_stream_message("generator", sdk_result)
  assert len(store.load(record.id).usage["entries"]) == 1
  log_stream_message("generator", sdk_result)
  assert len(store.load(record.id).usage["entries"]) == 1
  ```
- [ ] **Step 2: Confirm red.** Run `rtk pytest tests/agents/test_agents.py tests/execution/test_run_journal.py -q`; expect only the new usage assertions to fail.
- [ ] **Step 3: Implement result capture.** Add the context manager and `record_result` in `meow.usage`. Start the scope in `run_sprint` after `record` is created; reset it in `finally` around the remaining run flow. Invoke capture from `log_stream_message` and the reviewer's custom `ResultMessage` branch. Keep capture after a message is actually received, so a process crash before a result is not counted. Record a failed SDK result's reported usage before propagating the existing failure. Inspect every feature-run `ResultMessage` stream to avoid both omissions and double counting.

  ```python
  _active_usage = ContextVar("meow_active_usage", default=None)

  @contextmanager
  def usage_scope(store: RunStore, run_id: str):
      token = _active_usage.set((store, run_id))
      try:
          yield
      finally:
          _active_usage.reset(token)
  ```
- [ ] **Step 4: Confirm green and check role paths.** Run the two focused test files and `rtk ruff check` on modified files. Search `src/meow/agents` for all `ResultMessage` and `receive_response` paths and confirm every feature-run role is accounted for exactly once.
- [ ] **Step 5: Commit.** Stage only this task's files and commit with `feat: capture feature-run SDK usage`.

### Task 3: Render reliable status totals

**Files:** Modify `src/meow/status_cli/core.py` and `docs/CLI.md`; test `tests/cli/test_status_cli.py`.

**Interfaces:** `render(record: RunRecord, *, verbose: bool = False) -> str` keeps its existing signature. The default view shows known turns, tokens, and cost totals or `unavailable`; verbose output includes the per-role entry list and its source values.

- [ ] **Step 1: Write failing status tests.** Build a saved run with two usage entries and assert concise totals and verbose role details. Build one with legacy `usage="unavailable"` and assert all missing metrics display `unavailable`, not `0` or `$0.00`.

  ```python
  rendered = render(record, verbose=True)
  assert "Tokens: 14" in rendered
  assert "generator" in rendered
  assert "unavailable" in render(legacy_record)
  ```
- [ ] **Step 2: Confirm red.** Run `rtk pytest tests/cli/test_status_cli.py -q`; expect the new rendering assertions to fail.
- [ ] **Step 3: Render totals and document them.** Call `usage_totals(record.usage)`; format only known numeric values. Retain the existing phase, worktree, failure, and recovery fields. Update `docs/CLI.md` with the exact status behavior and data-availability limits.

  ```python
  totals = usage_totals(record.usage)
  tokens = totals["tokens"]
  lines.append(f"Tokens: {tokens if tokens is not None else 'unavailable'}")
  ```
- [ ] **Step 4: Verify.** Run `rtk pytest tests/cli/test_status_cli.py tests/test_usage.py tests/execution/test_run_state.py tests/agents/test_agents.py -q`, then the repository lint gate `rtk ruff check`. Review the final diff for accidental changes to unrelated local work.
- [ ] **Step 5: Commit.** Stage only this task's files and commit with `feat: show SDK usage in run status`.

## Completion check

Run the targeted tests above and the configured project gate. Inspect one synthetic saved run through `meow status RUN_ID --verbose` to confirm known metrics and a legacy run to confirm `unavailable`. This plan is complete only when the status display is backed by SDK `ResultMessage` data; creating fields without capture does not satisfy it.

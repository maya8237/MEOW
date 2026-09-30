# Reviewer-Agent Crash Retry & Diagnosability

**Goal:** When the reviewer's git subprocess calls or the Agent SDK's `claude` CLI subprocess crash (not a normal nonzero exit — an abnormal termination), retry a bounded number of times with backoff, and when retries are exhausted, raise an error that clearly names the role, the exit code, and any captured stderr — instead of a bare exit code bubbling up.

## Root-cause note

The underlying crash is external (a Node/CLI-level issue, most likely on Windows) and out of meow's control. This fix is resilience (retry) and diagnosability (clear errors), not a fix to the crash itself — consistent with what was asked.

## Design

**Crash detection** (`agents/base.py`, shared): `_looks_like_a_crash(exit_code)` returns True for a negative exit code (POSIX: killed by signal, e.g. `-11` for SIGSEGV; Windows: `subprocess`/`asyncio` commonly sign-extends a fast-fail NTSTATUS code like `0xC0000409` into a large negative number, e.g. `-1073740791`) or a code `>= 0x80000000` (in case it surfaces as the raw unsigned NTSTATUS value instead). A normal CLI never returns a negative or huge-unsigned code, so this reliably separates "crashed/killed" from "exited with a chosen nonzero status" on both platforms without needing OS-specific branching.

**SDK query retry** (`Agent.run_query`, `agents/base.py`): this is the *shared* one-shot query path every one-shot role uses (`ReviewerAgent`'s `review_prompt`/`review_merge_request`/`review_branch`/`review_plan` all call it, as do `PlannerAgent`, `GitlabFetcherAgent`, `IssueFetcherAgent`), so fixing it here covers "the review query" the user reported crashing, at the correct shared layer rather than a reviewer-only patch. `claude_agent_sdk.query()` is documented as stateless/one-shot ("fire and forget", no conversation carried over) — retrying by calling it fresh is safe: nothing meow-level has mutated when a crash happens mid-query, and a successful retry re-runs the same prompt/options from scratch (no partial-output corruption risk). Catches `claude_agent_sdk.ProcessError` specifically:
- Crash-looking (`_looks_like_a_crash(exc.exit_code)`) and attempts remain: log a warning, back off (`asyncio.sleep`, doubling), retry.
- Crash-looking but retries exhausted, OR not crash-looking at all (a normal `ProcessError`, e.g. a real nonzero exit): wrap in a `RuntimeError` naming the role, exit code, attempt count, and stderr, and raise. The existing "non-success `ResultMessage` → `RuntimeError`" path (the CLI itself reporting a graceful failure, e.g. max turns) is untouched — that's not a crash and retrying it wouldn't help.

**Git subprocess retry** (`agents/reviewer.py`): `_git_review_context` (4 git calls) and `_branch_diff` (2 git calls) share a new `_run_git_retrying(argv)` helper replacing their raw `subprocess.run(...)` calls — same crash-detection/backoff shape, using `time.sleep` (these are already synchronous functions called from async code, consistent with their existing design). `_branch_diff`'s existing raise-on-failure message gains an explicit exit-code number.

## Explicitly out of scope

The **persistent** `ClaudeSDKClient`-based agents (`Generator.implement`, `ReviewFixAgent.fix`, `LintFixAgent.fix`) are **not** touched. Retrying a crash mid-conversation there is a materially different, riskier problem — a new client loses the accumulated conversation context, and file edits already applied by earlier turns in the same session could be revisited inconsistently by a fresh session. The user's report and wording ("the reviewer-agent crash", "run the review query") point specifically at the one-shot review path; expanding to the multi-turn agents isn't asked for and isn't a safe drop-in extension of this same retry shape.

## Tests

- `_looks_like_a_crash`: negative codes (small POSIX-signal-style and large Windows-fast-fail-style), large unsigned NTSTATUS-style codes, normal codes (0, 1, 127), `None`.
- `Agent.run_query`: retries and eventually succeeds after N crash-looking failures; gives up and raises a clear error after exhausting retries; does not retry a non-crash `ProcessError`; existing success/graceful-failure tests keep passing unmodified.
- `_run_git_retrying`: retries and succeeds after a crash-looking result; gives up and returns the last (still-crashed) result after exhausting attempts; does not retry a normal nonzero exit.

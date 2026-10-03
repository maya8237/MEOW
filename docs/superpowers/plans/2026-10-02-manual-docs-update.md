# Manual Docs Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manually invoked `meow docs-update` command that updates project documentation on `dev` from changes since the previous docs update.

**Architecture:** A deterministic preflight validates the branch and Git state, resolves a saved baseline commit, and gathers a bounded code/documentation diff. A documentation agent edits only relevant prose files using that evidence. MEOW displays the final diff for review and writes a tracked baseline marker only after successful generation; it does not commit or push. Normal `run`, `plan`, and unattended feature flows never call this command or emit doc-update recommendations.

**Tech Stack:** Python, Git subprocesses, Claude Agent SDK, existing CLI/prompt patterns, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Manual documentation maintenance.”

## Global Constraints

- Command spelling is exactly `meow docs-update`.
- It is invoked manually on `dev`; it is not scheduled or called by `meow run`, `plan`, or `--unattended`.
- The command reports its comparison baseline and does not invent claims unsupported by code, tests, or existing docs.
- It shows the diff for review and does not automatically commit, push, or deliver it.
- First use requires an explicit `--since REF` when no valid saved baseline exists.

## Review Focus

- A missing, unreachable, or rewritten baseline must stop before document edits.
- A dirty checkout must not let the command mix unrelated changes into its diff.
- A user on a branch other than `dev` must receive a clear refusal.
- A failed agent run must not advance the baseline marker.
- The normal feature-run path must not call documentation-update code.

---

## File map

- Create `src/meow/docs_update/core.py`: branch/baseline preflight, Git diff, allowed-path validation, result.
- Create `src/meow/agents/docs_updater.py`: evidence-bounded documentation editing role.
- Modify `src/meow/cli/core.py`, `src/meow/prompts/core.py`, `docs/CLI.md`.
- Add a tracked marker `docs/.meow-docs-update.json` only when the command succeeds in a real project; tests create it in fixtures. It records the baseline commit used and the target HEAD inspected, without timestamps as a substitute for Git identity.
- Test `tests/docs_update/test_preflight.py`, `tests/docs_update/test_run.py`, `tests/cli/test_cli.py`.

### Task 1: Branch and baseline preflight

**Interface:** `prepare_docs_update(repo: Path, since: str | None) -> DocsUpdateInput` returns `baseline_sha`, `head_sha`, changed paths, and bounded diffs. It requires branch `dev`, a clean tree, and a baseline that is an ancestor of HEAD.

- [ ] Add failing fixture-repo tests for `dev`, wrong branch, dirty files, valid marker, first run without `--since`, invalid ref, and nonancestor ref. Assert no write occurs on preflight failure.
- [ ] Run `rtk pytest tests/docs_update/test_preflight.py -q` and confirm red.
- [ ] Implement Git subprocess calls with argument arrays, resolved paths, and a tracked marker format. On first use, require `--since REF`; after a successful update, the marker's inspected HEAD becomes the next baseline once committed.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: validate docs-update baseline`.

### Task 2: Evidence-bounded documentation edits

**Interface:** `run_docs_update(input: DocsUpdateInput) -> DocsUpdateResult` invokes one role restricted to approved documentation paths and returns changed files plus a diff. It rejects modifications outside those paths and leaves the marker unchanged on failure.

- [ ] Add failing tests with a fake agent for a justified CLI-doc edit, no relevant docs, an unsupported factual claim, an attempted code edit, and agent failure. Assert the final diff includes only permitted documentation files.
- [ ] Run `rtk pytest tests/docs_update/test_run.py -q` and confirm red.
- [ ] Implement the documentation role, prompt, allowed-path checks, and a reviewable diff. Include code/test evidence and uncertainty in the prompt; prohibit fabricated behavior claims. Make an empty relevant change a reported no-op, not a synthetic edit.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: update docs from reviewed code evidence`.

### Task 3: Public CLI and isolation from feature runs

**Interface:** `meow docs-update [--since REF] [-d PATH]` prints baseline, inspected HEAD, edited paths, and diff; exits nonzero when preflight or generation fails.

- [ ] Add failing CLI tests for help, first-run `--since`, normal marker use, wrong branch, and failure exit. Monkeypatch the updater to raise if invoked during `meow run` or `meow plan`, proving those commands are isolated.
- [ ] Run `rtk pytest tests/cli/test_cli.py tests/docs_update -q` and confirm red.
- [ ] Wire the parser and dispatch without adding an automatic docs hook. Document daily manual use and the need to review/commit the resulting diff and marker.
- [ ] Run focused tests, project lint, and a fixture-repo smoke invocation.
- [ ] Commit: `feat: expose manual docs-update command`.

## Completion check

In a fixture `dev` branch, run first with `--since`, review the generated diff/marker, commit them, then run again after a code change. Confirm the second run uses the saved inspected HEAD and that ordinary feature runs produce no documentation recommendations.

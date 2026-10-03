# Editor Hook Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MEOW's existing optional Claude Code hooks transparent during onboarding and easy to diagnose.

**Architecture:** Reuse the current reversible hook installer. Onboarding shows each selected hook, its host event, command, and effect before installation. A read-only diagnostic compares MEOW's installation manifest with host settings and reports active, missing, or modified entries. Runs remain independent of editor hooks.

**Tech Stack:** Python, existing `meow.hooks.claude`, Claude Code project settings JSON, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Editor hooks.”

## Global Constraints

- Hooks stay optional and reversible.
- Do not add a general event bus; that remains deferred in `docs/FUTURE_FEATURES.md`.
- A run, including `--unattended`, works without installed editor hooks.
- Diagnostics are read-only and do not interpret unrelated user hooks as MEOW-owned.

## Review Focus

- A modified user hook is never removed or overwritten by MEOW diagnostics.
- A missing settings file reports inactive hooks without creating it.
- Repeated installation does not duplicate hook entries.
- Hook visibility must not become a required run gate.
- A malformed manifest reports a diagnostic rather than deleting settings.

---

## File map

- Modify `src/meow/hooks/claude.py`, `src/meow/cli/core.py`, `skills/onboard/SKILL.md`, `docs/CLI.md`.
- Test `tests/hooks/test_claude.py`, `tests/cli/test_cli.py`.

### Task 1: Read-only hook inspection

**Interface:** `inspect_claude_hooks(project_dir: Path) -> dict[str, str]` maps each MEOW hook name to `active`, `missing`, or `modified` using the manifest and current settings.

- [ ] Add failing tests for fresh project, full installation, deleted entry, modified entry, malformed manifest, and unrelated user hook.
- [ ] Run `rtk pytest tests/hooks/test_claude.py -q` and confirm red.
- [ ] Implement comparison without writes and add `meow hooks status claude` CLI dispatch.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: inspect optional Claude hooks`.

### Task 2: Show onboarding proposal and preserve run independence

**Interface:** Onboarding lists exact host event, command, and effect for each selected hook before installation; run flow never requires hook presence.

- [ ] Add failing onboarding/CLI tests for an accepted subset, a declined install, and a normal unattended run with no hooks.
- [ ] Run `rtk pytest tests/hooks/test_claude.py tests/cli/test_cli.py -q` and confirm red.
- [ ] Update onboarding instructions and CLI guide; retain the existing idempotent installer and uninstaller.
- [ ] Run focused tests and project lint.
- [ ] Commit: `docs: make optional hook setup transparent`.

## Completion check

Install a subset in a fixture project, inspect it, alter one entry, and inspect again. Confirm status detects the modification and ordinary `meow run` behavior is unaffected.

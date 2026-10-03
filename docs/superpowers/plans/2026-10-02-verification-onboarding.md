# Verification and Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make onboarding configure verified lint/test/build/browser commands and make every run report trustworthy final verification evidence.

**Architecture:** Extend existing config discovery and check execution, not the test runner. Onboarding proposes commands, runs a safe preflight with the user's approval, and persists exact settings. At run completion, MEOW executes required gates against the final revision and records distinct results. Browser validation uses the project's provider and managed dev server, with screenshots/logs as evidence when available.

**Tech Stack:** Python, existing `checks`, `test_runner`, `frontend`, tester MCP/skill adapters, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Verification gates and lint” and “Browser validation.”

## Global Constraints

- Keep existing `[[lint]]`, tester test commands, and `[[build]]` semantics compatible.
- Required gates use the final combined code revision; no stale pass is accepted.
- Missing browser capability is reported as unverified, never passed.
- Onboarding shows exact commands and files before saving setup; `--unattended` runs only preconfigured actions.
- No universal lint rules or MEOW-owned browser engine.

## Review Focus

- A post-review edit invalidates an earlier passing gate.
- A configured browser provider that cannot start never reports a pass.
- A dev server started by MEOW is stopped on failure/cancellation.
- A linter auto-fix that changes code triggers a fresh final gate.
- A monorepo command runs in its configured `cwd`, not the repo root.

---

## File map

- Modify `src/meow/checks/core.py`, `src/meow/test_runner/core.py`, `src/meow/config/core.py` for final evidence and preflight.
- Modify `src/meow/frontend.py`, `src/meow/agents/tester.py` for browser capability and evidence.
- Modify `skills/onboard/SKILL.md`, `templates/harness.toml.example`, `templates/harness.toml.typescript.example`, `docs/CLI.md`.
- Test `tests/execution/test_checks.py`, `tests/testing/test_test_runner.py`, `tests/agents/test_tester.py`, `tests/test_config.py`, `tests/test_frontend.py`.

### Task 1: Verify onboarding proposals before saving

**Interface:** `preflight_check(project, check) -> PreflightResult` runs the exact proposed command with configured `cwd`, timeout, and environment, without auto-fixing or modifying configuration. Onboarding presents results and saves only accepted commands.

- [ ] Add failing tests for valid lint/build/test commands, missing executable, wrong `cwd`, timeout, and a command whose preflight would mutate tracked files. Assert onboarding does not silently save a failed command.
- [ ] Run `rtk pytest tests/execution/test_checks.py tests/test_config.py -q` and confirm red.
- [ ] Implement read-only command validation where possible and explicit user review for commands with side effects. Update the onboarding skill to show the exact command and preflight result.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: preflight onboarding verification commands`.

### Task 2: Clarify lint feedback and final gate status

**Interface:** Per-file lint feedback contains current remaining findings after any fix; final `CheckResult` remains tied to revision and config fingerprint.

- [ ] Add failing tests for auto-fix followed by a clean result, auto-fix with remaining errors, and a changed file after an earlier pass. Assert status distinguishes lint/test/build outcomes.
- [ ] Run `rtk pytest tests/execution/test_checks.py tests/cli/test_status_cli.py -q` and confirm red.
- [ ] Extend the current lint hook and status formatter without introducing another linter layer. Reuse `checks_current` after the last edit.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: report final verification evidence`.

### Task 3: Browser preflight and run evidence

**Interface:** `BrowserCapability` records configured start command, readiness URL, provider, and required status. Browser validation emits result, tested flows, and paths to artifacts under the run record.

- [ ] Add failing tests for a working provider, missing start command, readiness timeout, provider failure, screenshot/log capture, and cancellation cleanup. A required browser failure must block completion.
- [ ] Run `rtk pytest tests/test_frontend.py tests/testing/test_test_runner.py tests/agents/test_tester.py -q` and confirm red.
- [ ] Extend existing frontend discovery and tester stage; use the configured Playwright or equivalent provider, existing managed-server lifetime, and a bounded artifact directory. Do not add a general browser-control CLI.
- [ ] Run focused tests, project lint, and a fixture web app smoke run when the test environment supports one.
- [ ] Commit: `feat: verify configured browser flows`.

## Completion check

Inspect a run with lint, test, build, and browser support and one without browser support. The first must show separate current results; the second must say browser validation was unavailable or unconfigured, never PASS.

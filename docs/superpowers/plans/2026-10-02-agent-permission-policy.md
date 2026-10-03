# Agent Permission Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply project-configured, role-scoped agent permissions to every feature-run phase, including unattended runs.

**Architecture:** Parse a small policy from `.harness.toml`, compile it to the installed Claude Agent SDK's `can_use_tool` callback and `disallowed_tools`, and inject it through the shared `Agent.options` path. A denied or approval-required action in unattended mode is recorded as a run failure requiring user action; MEOW never prompts. Keep SDK enforcement authoritative and reject policy forms the SDK cannot enforce.

**Tech Stack:** Python, `tomllib`, Claude Agent SDK `ClaudeAgentOptions.can_use_tool`, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Permission policy.”

## Global Constraints

- No interactive permission prompt in `--unattended`, including knowledge and shaping phases.
- A policy must fail closed when malformed or unsupported; never pretend to enforce an unsupported command/path restriction.
- Do not change the current default role tool lists when no policy is configured.
- Credentials and tool inputs must not be copied into checkpoint error text.

## Review Focus

- Windows and POSIX path spellings must not bypass a denied path.
- A `Bash` command with separators or shell expansion must not be judged safe from its first word alone.
- An unknown tool must not become allowed because a wildcard matched another role.
- A denied action must not be retried forever by an unattended agent.
- A reviewer must retain its current read-only tool boundary without policy configuration.

---

## File map

- Create `src/meow/permissions.py`: policy schema, normalization, role selection, SDK callback.
- Modify `src/meow/config/core.py`: parse and validate `[permissions]` and `[[permissions.rule]]`.
- Modify `src/meow/agents/base.py`: attach compiled policy to `ClaudeAgentOptions`.
- Modify `src/meow/sprint_runner/core.py`, `src/meow/run_state/core.py`: mark unattended mode and record permission stops.
- Modify `templates/harness.toml.example`, `docs/CLI.md`: supported policy examples and limits.
- Test `tests/test_permissions.py`, `tests/test_config.py`, `tests/agents/test_agents.py`, `tests/execution/test_run_journal.py`.

### Task 1: Validate a deliberately small policy

**Interface:** `parse_policy(raw: object) -> PermissionPolicy`; `PermissionPolicy.for_role(role: str) -> RolePolicy`. Rules express a role, exact tool name, action (`allow`, `deny`, `ask`), and optional normalized path prefix. Command restrictions are accepted only if a complete SDK-enforceable rule is available; otherwise config validation rejects them.

- [ ] Add failing tests for role isolation, duplicate/conflicting rules, path escape, invalid action, and unsupported command patterns. Example: `parse_policy({"rule": [{"role": "generator", "tool": "Write", "action": "deny", "path": ".env"}]})` denies that path only for the generator.
- [ ] Run `rtk pytest tests/test_permissions.py tests/test_config.py -q` and confirm the new assertions fail.
- [ ] Implement `permissions.py` and config normalization in `config/core.py`; preserve the existing no-policy default.
- [ ] Run the focused tests and `rtk ruff check src/meow/permissions.py src/meow/config/core.py tests/test_permissions.py tests/test_config.py`.
- [ ] Commit only this slice: `feat: validate role-scoped permission policy`.

### Task 2: Enforce through SDK options

**Interface:** `make_permission_callback(policy: RolePolicy, *, unattended: bool)` returns an async callback yielding SDK `PermissionResultAllow` or `PermissionResultDeny`. `Agent.options` passes it as `can_use_tool` for configured roles.

- [ ] Add failing tests that invoke the callback with `Write`, `Read`, and `Bash` inputs, including mixed-case paths and shell metacharacters. Assert unknown or unparseable inputs are denied when a restricting rule applies. Assert no policy leaves current `ClaudeAgentOptions` unchanged.
- [ ] Run `rtk pytest tests/test_permissions.py tests/agents/test_agents.py -q` and confirm red.
- [ ] Implement SDK callback injection in `Agent.options`; ensure custom reviewer options and explorer definitions use the same policy or explicitly retain narrower existing tools. Do not add a second tool dispatcher.
- [ ] Run focused tests and project `rtk ruff check`.
- [ ] Commit: `feat: enforce project policy in SDK roles`.

### Task 3: Report unattended denials and document setup

**Interface:** A denied required action leaves the run checkpoint non-complete with a sanitized reason and recovery instruction; `meow status` displays the stop.

- [ ] Add a failing run test that simulates a denied generator edit under `--unattended`; assert no input read, no delivery, preserved worktree, and an actionable status reason. Add a second test proving knowledge or planner denial follows the same rule.
- [ ] Run `rtk pytest tests/execution/test_run_journal.py tests/agents/test_agents.py -q` and confirm red.
- [ ] Thread unattended mode into policy creation and journal a denial without logging raw tool input. Document supported rule syntax and its limits in the template and CLI guide.
- [ ] Run targeted tests, `rtk ruff check`, and inspect the diff for privilege broadening.
- [ ] Commit: `feat: report unattended permission stops`.

## Completion check

Run the focused tests and project lint gate. Inspect actual `ClaudeAgentOptions` for planner, generator, reviewer, fixer, and tester under both configured and absent policy. A configuration that claims unenforceable restrictions must fail before an agent starts.

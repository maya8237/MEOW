# Unify Agent Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Give MEOW's explorer, planner, generator, and reviewer one consistent class-based structure while preserving their current workflows and public CLI behavior.

**Architecture:** Add a small shared `AgentContext` for project-level settings (configuration and active project directory). The shared agent base depends on that generic context—not on sprint workflow state—and builds `ClaudeAgentOptions` plus one-shot SDK queries consistently. Each role module exposes a named `*Agent` class with a role-specific operation; the explorer remains declarative because the SDK consumes it as an `AgentDefinition`, while the generator keeps its persistent client/context-manager lifecycle. Sprint orchestration adapts its state to `AgentContext`; the `cr` reviewer can be constructed from project configuration alone, with no sprint object or plan file.

**Tech Stack:** Python 3.12+, `claude-agent-sdk`, `unittest`/`unittest.mock`, Ruff.

**Spec:** User request: “every agent has a different pythonic structure than the other and thats just weird. create a unified structure for agents.” Preserve current behavior in `src/meow/agents/` and `src/meow/orchestrator.py`.

## Global Constraints

- Do not invoke the `meow` CLI or harness during implementation or verification.
- Preserve the four role responsibilities, prompts, tool permissions, model selection, active working directory, lint hook, and reviewer verdict format.
- Keep generator sessions reusable across feedback rounds; do not replace its persistent `ClaudeSDKClient` with a new client per round.
- Keep `Sprint` for sprint workflow state; the shared agent base consumes a generic `AgentContext` protocol and must not require or store `Sprint`.
- The `cr` review operation can run from generic project configuration and directory context, without a sprint or sprint plan. Task 5 supplies that context directly.
- Preserve existing imports from `meow.orchestrator` that tests and callers currently use, unless repository evidence proves they are internal and can safely be migrated.
- Run only focused Python unit tests and Ruff checks; do not run an end-to-end sprint.

## Review Focus

- Explorer's `AgentDefinition` must preserve description, prompt, tools, and configured model; cover it in `tests/test_agents.py`.
- Every role must use `AgentContext.active_working_dir()` and its role model; cover options passed to the SDK in planner, reviewer, and generator tests.
- SDK result messages with non-success subtypes must still raise a role-specific `RuntimeError`; cover one-shot roles and retain generator response behavior.
- Reviewer output must still parse missing or malformed `STATUS` as `FAIL`, and both contract and prompt-review entry points must still write/read the same review paths; cover parsing and prompt construction.
- Existing orchestration imports and feature-round behavior must remain compatible; retain the current identity/re-export test and add constructor delegation assertions.

---

## File Structure

- Create `src/meow/agents/base.py`: `AgentContext` protocol, generic `Agent` base for config/directory-based SDK options and one-shot queries, and later a concrete `ProjectContext` for non-sprint flows.
- Modify `src/meow/agents/explorer.py`: define `ExplorerAgent`; its `definition()` returns the SDK's `AgentDefinition`.
- Modify `src/meow/agents/planner.py`: define `PlannerAgent`; its `run(feature_name, request)` creates the plan and executes the configured query.
- Modify `src/meow/agents/generator.py`: define `GeneratorAgent`; keep its async context manager and `implement(instruction)` API, using base option construction.
- Modify `src/meow/agents/reviewer.py`: define `ReviewerAgent`; its `review_plan(plan_file)` and `review_prompt(prompt)` expose the existing operations.
- Modify `src/meow/agents/__init__.py`: export the four named role classes and shared base if public export is useful; avoid wrapper implementations here.
- Modify `src/meow/orchestrator.py`: construct role classes and call their operations; retain compatibility names as direct aliases for existing role implementations.
- Create `tests/test_agents.py`: focused unit coverage for consistent setup and preserved role behavior.
- Modify `tests/test_cli.py`: update existing monkeypatch targets only where needed and preserve the compatibility/re-export assertion.

### Task 1: Add the shared agent base

**Files:**
- Create: `src/meow/agents/base.py`
- Create: `tests/test_agents.py`

**Interfaces:**
- Produces: `AgentContext` protocol for model and active-directory access, plus `Agent(context: AgentContext)` with `options(*, system_prompt: str, allowed_tools: list[str], role: str, **extra_options) -> ClaudeAgentOptions` and `async run_query(prompt: str, options: ClaudeAgentOptions, role: str) -> None`.
- Consumes: `AgentContext.model(role)`, `AgentContext.active_working_dir()`, and `claude_agent_sdk.query` / `ResultMessage`.

- [x] **Step 1: Add failing base tests**

Test that a plain project `AgentContext` (not a `Sprint`) lets option construction select the requested role model and active working directory, forwards caller-provided SDK options, and that a non-success `ResultMessage` raises `RuntimeError` naming the role.

- [x] **Step 2: Run the focused tests and confirm they fail**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v`
Expected: failures because `meow.agents.base.Agent` does not exist.

- [x] **Step 3: Implement `Agent`**

Implement a minimal base class storing generic `AgentContext`. `options()` constructs `ClaudeAgentOptions` using the provided prompt/tools, `self.context.model(role)`, `str(self.context.active_working_dir())`, and extra role options. `run_query()` iterates `query(prompt=..., options=...)` and raises `RuntimeError(f"{role} failed: {message.subtype}")` for non-success `ResultMessage` values.

- [x] **Step 4: Run the focused tests**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v`
Expected: PASS.

### Task 2: Standardize explorer and planner classes

**Files:**
- Modify: `src/meow/agents/explorer.py`
- Modify: `src/meow/agents/planner.py`
- Modify: `tests/test_agents.py`

**Interfaces:**
- Produces: `ExplorerAgent(context).definition() -> AgentDefinition` and `PlannerAgent(context).run(feature_name: str | None, request: str) -> Path`.
- Compatibility: keep `make_explorer_agent(config, working_dir)` and `run_planner(sprint, feature_name, request)` as thin delegations until orchestrator callers and downstream imports have migrated.

- [x] **Step 1: Add role tests**

Assert `ExplorerAgent.definition()` retains the exact current role description, prompt, tools, and explorer model. Mock planner `query`; assert `PlannerAgent.run()` writes under `active_working_dir()/docs_dir`, supplies the planner model/cwd, registers the explorer definition, and raises on SDK failure.

- [x] **Step 2: Run role tests and confirm the new class assertions fail**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v`
Expected: failures because the role classes are not yet present.

- [x] **Step 3: Implement the role classes**

Move current role setup into the classes. `PlannerAgent.run()` creates the output directory and filename exactly as it does now, builds role options through `Agent.options()`, adds `agents={"explorer": ExplorerAgent(self.context).definition()}`, then delegates SDK iteration to `run_query()`.

- [x] **Step 4: Keep thin compatibility functions and run tests**

Delegate the old functions to the new classes without duplicating setup. Run the focused tests and expect PASS.

### Task 3: Standardize the persistent generator class

**Files:**
- Modify: `src/meow/agents/base.py` (declare explorer/lint dependencies in `AgentContext`)
- Modify: `src/meow/agents/generator.py`
- Modify: `tests/test_agents.py`

**Interfaces:**
- Produces: `GeneratorAgent(context, plan_file)` with `async __aenter__`, `async __aexit__`, and `async implement(instruction) -> str`.
- Compatibility: retain `Generator` as a direct alias to `GeneratorAgent`.

- [x] **Step 1: Add a generator lifecycle test**

Mock `ClaudeSDKClient`; verify entering creates/enters one client configured with generator model, explorer definition, lint hook, and active working directory; verify `implement()` sends the instruction and returns joined `TextBlock` contents.

- [x] **Step 2: Run the focused test and confirm it fails**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v`
Expected: the new `GeneratorAgent` test fails before the class is introduced.

- [x] **Step 3: Implement `GeneratorAgent`**

Subclass `Agent`; build its options with the common helper while preserving its existing prompt, tool list, explorer registration, and `PostToolUse` lint hook. Keep a single `ClaudeSDKClient` alive for the context-manager lifetime and preserve response text extraction.

- [x] **Step 4: Run generator tests**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v`
Expected: PASS, including exactly one client instance across multiple `implement()` calls.

### Task 4: Standardize reviewer operations

**Files:**
- Modify: `src/meow/agents/base.py` (complete `AgentContext` protocol)
- Modify: `src/meow/agents/reviewer.py`
- Modify: `tests/test_agents.py`

**Interfaces:**
- Produces: `ReviewerAgent(context)` with `review_plan(plan_file: Path)` and `review_prompt(prompt: str | None)`; prompt review uses only generic project context, with no `Sprint`, plan file, or contract dependency.
- Compatibility: keep `run_reviewer(sprint, plan_file)` and `run_prompt_reviewer(sprint, prompt)` as thin delegations.

- [x] **Step 1: Add reviewer tests**

Test `_verdict_status` for PASS, FAIL, and missing status. Mock query and file reads to verify both operations preserve their current target paths, allowed tools, lint and architecture instructions, prompt-review git context, and result parsing.

- [x] **Step 2: Run the focused tests and confirm new class tests fail**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agents.py -v`
Expected: failures because `ReviewerAgent` is not yet defined.

- [x] **Step 3: Implement `ReviewerAgent`**

Move shared option/query setup into the class and use `Agent.options()` plus `run_query()`. Preserve helper behavior and both review file naming conventions exactly.

- [x] **Step 4: Keep compatibility functions and run tests**

Delegate both legacy functions to class methods. Run focused reviewer tests and expect PASS.

### Task 5: Rewire orchestration and document the agent contract

**Files:**
- Modify: `src/meow/agents/base.py` (add concrete `ProjectContext`)
- Modify: `src/meow/agents/__init__.py`
- Modify: `src/meow/orchestrator.py`
- Modify: `tests/test_cli.py`
- Modify: `AGENTS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `ExplorerAgent`, `PlannerAgent`, `GeneratorAgent`, and `ReviewerAgent`.
- Produces: one predictable construction pattern: role classes receive generic `AgentContext`; sprint-only coordination remains in the orchestrator; SDK options and one-shot error handling come from the common base. The `cr` path constructs a concrete generic `ProjectContext` from config and project directory, then constructs the reviewer directly. Explorer exposes its SDK definition because it runs as a nested subagent.

- [x] **Step 1: Add orchestration delegation assertions**

Update the existing role re-export test to assert the orchestrator's compatibility names still point to the role implementations; add assertions that `_build_sprint()` builds the explorer definition through `ExplorerAgent` and orchestration calls class operations.

- [x] **Step 2: Rewire orchestration**

Replace internal calls with role class operations while keeping compatibility aliases at the current module boundary. Do not change CLI arguments, round ordering, retry limits, paths, logs, or review summaries.

- [x] **Step 3: Update contributor documentation**

Update `AGENTS.md` and `README.md` layout/architecture descriptions to explain the shared base, the class pattern, and the explorer's declarative SDK role. Do not mention or run the harness.

- [x] **Step 4: Run focused tests and lint**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests -p test_agents.py -v` and `.venv/Scripts/python.exe -m unittest discover -s tests -p test_cli.py -v`
Expected: PASS.

Run: `.\.venv\Scripts\ruff.exe check src/meow/agents src/meow/orchestrator.py tests/test_agents.py tests/test_cli.py`
Expected: `All checks passed!`.

- [x] **Step 5: Review the diff manually**

Confirm agent prompts/tool lists are unchanged, all role SDK options use the shared base, compatibility names remain direct aliases, and no end-to-end sprint command was run.

## Self-Review

- Spec coverage: the four current role modules each receive a consistent class shape and shared option/query setup; role-specific lifecycles remain explicit where needed.
- Placeholder scan: no TBD/TODO steps; test cases and commands are specified.
- Type consistency: `Sprint` is the constructor dependency; planner/reviewer/generator operations are declared above and used consistently.
- Review Focus: each identified behavior has a corresponding task-level test.












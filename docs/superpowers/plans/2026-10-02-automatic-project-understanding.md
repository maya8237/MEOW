# Automatic Project Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one `meow run` perform relevant knowledge gathering, optional shaping, and optional breadboarding without user-managed preparatory commands.

**Architecture:** Introduce a bounded pre-plan assessment that reads repository evidence, decides which optional artifacts are justified, and writes one internal context record. The planner receives that record; run checkpoints identify pre-plan subphases. Existing `knowledge`, `shape`, `plan_state`, and prompt modules remain the underlying facilities. No feature run proposes or edits documentation.

**Tech Stack:** Python, existing Claude Agent SDK roles, `RunStore`, `KnowledgeAudit`, shape artifacts, pytest, ruff.

**Spec:** [Agreed product specification](../specs/2026-10-02-harness-master-agreed-product-spec.md), “Project understanding and design inside a run.” Depends on permission policy and cancellation applying to these phases.

## Global Constraints

- Clear requests retain the direct plan path.
- Knowledge and shaping are internal phases of `run`, not required user commands.
- `--unattended` never asks a question. Reversible choices may use evidence; unresolved consequential product choices checkpoint and stop.
- Feature runs do not recommend, draft, edit, or deliver documentation updates.
- One canonical plan remains authoritative; artifacts are bounded and linked to the run.

## Review Focus

- A repository missing `AGENTS.md` must not cause auto-scaffolding during a feature run.
- A clear one-line fix must not acquire a shaping/breadboard delay.
- A broad request with conflicting product outcomes must stop unattended before mutation.
- A malformed pre-plan artifact must fail safely and retain the checkpoint.
- A cancelled pre-plan phase must not start the planner afterward.

---

## File map

- Create `src/meow/preplan.py`: assessment, decision, and artifact orchestration.
- Modify `src/meow/knowledge/core.py`: bounded evidence extraction usable by the pre-plan phase.
- Modify `src/meow/shaping/core.py`: decision and reflection data, retaining existing artifact compatibility.
- Modify `src/meow/sprint_runner/core.py`, `src/meow/prompts/core.py`, `src/meow/run_state/core.py`: phase flow and context injection.
- Modify `src/meow/plan_state.py`, `docs/CLI.md`, `skills/run/SKILL.md`, `skills/onboard/SKILL.md`, `src/meow/knowledge_documents/core.py`.
- Test `tests/execution/test_preplan.py`, `tests/test_shaping.py`, `tests/knowledge/test_knowledge.py`, `tests/architecture/test_prompts.py`.

### Task 1: Internal knowledge context without doc actions

**Interface:** `gather_context(repo: Path, request: str) -> ProjectContextEvidence` returns cited architecture, code/test references, relevant constraints, and uncertainty. It is read-only and bounded in size.

- [ ] Add failing tests for relevant evidence selection, missing docs, stale links, and zero filesystem writes. A missing doc may appear as internal uncertainty but must not appear as a run-time documentation recommendation.
- [ ] Run `rtk pytest tests/execution/test_preplan.py tests/knowledge/test_knowledge.py -q` and confirm red.
- [ ] Implement `gather_context` using existing audit and repository search helpers, with a maximum evidence count and deterministic ordering. Do not invoke `knowledge create`.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: gather project evidence inside runs`.

### Task 2: Automatic shaping decision and safe unattended outcome

**Interface:** `assess_preplan(request, evidence) -> PreplanDecision` yields `direct`, `shape`, or `needs_user_decision` with reasons. A shape artifact records problem, constraints, options, fit checks, chosen approach, and assumptions.

- [ ] Add failing tests for a clear fix, an ambiguous but reversible design, and two incompatible product outcomes. Assert unattended never invokes `input()` and the latter checkpoints before generator work.
- [ ] Run `rtk pytest tests/execution/test_preplan.py tests/test_shaping.py -q` and confirm red.
- [ ] Implement the decision boundary using repository evidence and a bounded agent assessment where heuristics are insufficient. Persist selected assumptions and cite the decision in planner context. Keep existing explicit `--shape` input supported.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: shape uncertain work within run`.

### Task 3: Conditional breadboard and reflection

**Interface:** `needs_breadboard(shape, evidence) -> bool`; `reflect_breadboard` returns findings tied to named places, affordances, or wires. Accepted vertical slices reach planning.

- [ ] Add failing tests for a simple CLI change (skip), cross-component UI flow (run), missing error/authorization path, inconsistent names, and an untestable slice. Assert findings identify the affected element.
- [ ] Run `rtk pytest tests/test_shaping.py tests/execution/test_preplan.py -q` and confirm red.
- [ ] Extend the existing breadboard artifact/reflection and planner prompt. Keep the artifact internal to the run unless a user inspects it.
- [ ] Run focused tests and `rtk ruff check`.
- [ ] Commit: `feat: reflect complex run designs before planning`.

### Task 4: Phase integration and automatic plan lifecycle

**Interface:** `run_sprint` transitions through pre-plan phases, then `planning`; `PlanStore` transitions automatically and captures material discoveries.

- [ ] Add failing end-to-end stub tests for direct, shaped, breadboarded, cancelled, and unattended-needs-decision runs. Assert every path leaves a useful status and only successful paths reach implementation.
- [ ] Run `rtk pytest tests/execution/test_preplan.py tests/execution/test_run_journal.py -q` and confirm red.
- [ ] Wire pre-plan into `run_sprint` before planner invocation. Update plan lifecycle at actual boundaries, and record concise discovery notes without creating duplicate plans.
- [ ] Run focused tests, project lint, and inspect `meow status` output for each stub path.
- [ ] Commit: `feat: integrate automatic pre-plan phases`.

### Task 5: Evidence-backed knowledge setup in onboarding

**Interface:** Onboarding presents a concise `AGENTS.md` index and only linked project documents justified by code, tests, or existing decisions. Document creation remains an explicit onboarding choice; feature runs do not create docs.

- [ ] Add failing tests for an established repo with relevant existing docs, a repo with missing architecture guidance, a broken link, and a repo where no extra document is justified. Assert onboarding never creates empty templates or invents project policy.
- [ ] Run `rtk pytest tests/knowledge tests/native/test_native_skills.py -q` and confirm the new assertions fail.
- [ ] Extend the current knowledge audit and selected-document writer to cite concrete repository evidence, mark uncertainty, and propose only useful links. Update onboarding instructions to present the exact proposed files before writing.
- [ ] Run focused tests and project lint.
- [ ] Commit: `feat: establish selective project knowledge on onboarding`.

## Completion check

Use mocked SDK roles to prove a single `meow run` chooses all three paths correctly. Inspect its artifacts and checkpoint after each phase. Verify no feature-run path writes or recommends prose documentation.

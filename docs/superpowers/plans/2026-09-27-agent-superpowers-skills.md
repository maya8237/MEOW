# Assign Superpowers Skills to MEOW Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Give each MEOW agent only the installed Superpowers skills that fit its role, and explain how those skills guide its work.

**Architecture:** Use the Claude Agent SDK skill configuration already available in the installed SDK. Set `AgentDefinition.skills` for the explorer and `ClaudeAgentOptions.skills` for planner, generator, and reviewer sessions. Keep skill identifiers and usage role-specific, and constrain the planner to MEOW's configured plan path and Sprint Contract workflow.

**Tech Stack:** Python 3.12+, Claude Agent SDK, pytest, Markdown.

**Spec:** User request in this conversation: assume the known Superpowers skills exist in the Claude SDK, assign relevant existing skills to all applicable MEOW agents, explain how agents will use them, and do not run `meow`.

## Global Constraints

- Do not invoke `meow` for planning, implementation, or review.
- Use only installed Superpowers skills with a demonstrated fit: `superpowers:writing-plans`, `superpowers:executing-plans`, `superpowers:receiving-code-review`, `superpowers:systematic-debugging`, `superpowers:test-driven-development`, and `superpowers:verification-before-completion`.
- Assign skills through the Claude Agent SDK (`AgentDefinition.skills` for explorer; `ClaudeAgentOptions.skills` for the other agent sessions).
- Planner output stays under the configured `docs_dir`, retains a numbered task list and `## Sprint Contract`, and does not add a human execution-method handoff.
- Preserve all pre-existing unrelated working-tree changes and untracked files.
- Inspect the in-progress agent-class refactor in the working tree before editing; integrate skill configuration with its current agent APIs and preserve its changes.
- Document the role-to-skill mapping and how each agent uses its skills.

## Review Focus

- Skill identifiers must be exact plugin-qualified names and must be attached to the intended role; pin this with assertions for the explorer definition and all three session option objects in `tests/test_agent_skills.py`.
- Planner skill guidance must not redirect plans to the skill's default path or add its interactive execution handoff; pin this with assertions on the planner prompt and existing configured output path in `tests/test_agent_skills.py`.
- Generator skill configuration must retain its explorer delegation and post-edit lint hook, and its `executing-plans` use must leave worktree and review-round control with MEOW; pin this with assertions on generator options and prompt in `tests/test_agent_skills.py`.
- Both reviewer entry points must receive verification guidance and retain their existing verdict-file format; pin this with assertions for `run_reviewer` and `run_prompt_reviewer` in `tests/test_agent_skills.py`.
- The README mapping must match the actual SDK configuration and explain each skill's practical use; compare each documented role against the role configuration exercised by `tests/test_agent_skills.py` during the task's documentation review step.

## Skill Fit Review

The local Superpowers plugin contains 15 skills. Assign the six with a direct role fit and omit the nine whose triggers or workflows conflict with MEOW's agent boundaries:

| Agent | Skills | Use in MEOW |
|---|---|---|
| Explorer | `superpowers:systematic-debugging` | Apply only to bug investigations; gather evidence and report likely causes while remaining read-only. |
| Planner | `superpowers:writing-plans` | Borrow task breakdown, file mapping, testability, and self-review; keep MEOW's configured output path, Sprint Contract, and automated handoff. |
| Generator | `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:receiving-code-review`, `superpowers:verification-before-completion` | Execute one planned task at a time; use RED/GREEN for code changes; diagnose failures before edits; verify reviewer findings before fixing; report actual verification evidence. Leave worktree setup, ledger/commit policy, review-round control, and termination to MEOW. |
| Reviewer | `superpowers:verification-before-completion` | Independently check actual code and command evidence against each criterion; produce the existing verdict without editing implementation. |

Omit `superpowers:brainstorming` because it requires interactive design approval and the planner is an autonomous stage; `superpowers:diagnosing-superpowers` because it diagnoses the Superpowers workflow itself; `superpowers:dispatching-parallel-agents` because MEOW's implementation and review rounds share sequential state; `superpowers:finishing-a-development-branch` because MEOW agents do not integrate or merge branches; `superpowers:requesting-code-review` because MEOW already runs its reviewer; `superpowers:subagent-driven-development` because its per-task implementer/reviewer dispatch duplicates the existing orchestrator; `superpowers:using-git-worktrees` because worktree lifecycle belongs to the orchestrator; `superpowers:using-superpowers` because it is a conversation-start meta skill, not role guidance; and `superpowers:writing-skills` because these agents do not create or maintain skills.

The local plugin contains `superpowers:executing-plans`; no skill named `implementing-plans` is installed. `executing-plans` is relevant to the generator, with its workspace setup, ledger, commit, and finalization steps explicitly subordinated to MEOW's orchestrator.

---

### Task 1: Configure and document role-specific skills

**Files:**
- Modify: `src/meow/agents/explorer.py`
- Modify: `src/meow/agents/planner.py`
- Modify: `src/meow/agents/generator.py`
- Modify: `src/meow/agents/reviewer.py`
- Modify: `README.md`
- Create: `tests/test_agent_skills.py`

**Interfaces:**
- Consumes: `AgentDefinition` and `ClaudeAgentOptions` from the installed `claude_agent_sdk`; existing `Sprint` role/model/explorer fields and configured plan path.
- Produces: explorer skill list `['superpowers:systematic-debugging']`; planner skill list `['superpowers:writing-plans']`; generator skill list `['superpowers:executing-plans', 'superpowers:test-driven-development', 'superpowers:systematic-debugging', 'superpowers:receiving-code-review', 'superpowers:verification-before-completion']`; reviewer skill list `['superpowers:verification-before-completion']` for both review entry points.

- [x] **Step 1: Add failing tests for skill assignment and role behavior**

Create `tests/test_agent_skills.py`. Use temporary directories and mocked SDK entry points to capture options without launching Claude. Cover the explorer definition, planner query options, generator client options, `run_reviewer`, and `run_prompt_reviewer`.

```python
import asyncio
from pathlib import Path
from unittest.mock import patch

from meow.agents import explorer, generator, planner, reviewer
from meow.sprint import Sprint


def make_sprint(tmp_path: Path) -> Sprint:
    return Sprint(
        repo_dir=tmp_path,
        config={
            "models": {
                "explorer": "test-model",
                "planner": "test-model",
                "generator": "test-model",
                "reviewer": "test-model",
            },
            "lint": [],
            "docs_dir": "docs/exec-plans/active",
            "max_rounds": 1,
        },
        explorer=explorer.make_explorer_agent(
            {"models": {"explorer": "test-model"}}, tmp_path
        ),
        lint_hook=None,
        working_dir=tmp_path,
    )


def test_explorer_uses_debugging_skill(tmp_path):
    agent = explorer.make_explorer_agent(
        {"models": {"explorer": "test-model"}}, tmp_path
    )
    assert agent.skills == ["superpowers:systematic-debugging"]


def test_planner_uses_writing_plans_with_meow_constraints(tmp_path):
    async def empty_query(*args, **kwargs):
        if False:
            yield None

    with patch("meow.agents.base.query", side_effect=empty_query) as query:
        result = asyncio.run(
            planner.run_planner(make_sprint(tmp_path), "feature", "Request")
        )

    options = query.call_args.kwargs["options"]
    assert result == tmp_path / "docs/exec-plans/active/feature.md"
    assert options.skills == ["superpowers:writing-plans"]
    assert options.agents["explorer"] is not None
    assert "configured docs_dir" in options.system_prompt
    assert "Sprint Contract" in options.system_prompt
    assert "execution-method handoff" in options.system_prompt


def test_generator_uses_implementation_skills_and_keeps_hooks(tmp_path):
    with patch("meow.agents.generator.ClaudeSDKClient") as client:
        generator.Generator(make_sprint(tmp_path), tmp_path / "plan.md")

    options = client.call_args.args[0]
    assert options.skills == [
        "superpowers:executing-plans",
        "superpowers:test-driven-development",
        "superpowers:systematic-debugging",
        "superpowers:receiving-code-review",
        "superpowers:verification-before-completion",
    ]
    assert options.agents["explorer"] is not None
    assert "PostToolUse" in options.hooks
    assert "one task at a time" in options.system_prompt
    assert "orchestrator" in options.system_prompt
    assert "ledger or commits" in options.system_prompt


def test_both_reviewers_use_verification_skill_and_keep_verdict_format(tmp_path):
    sprint = make_sprint(tmp_path)

    async def capture_review(prompt, options):
        review_path = Path(options.system_prompt.split("Write your verdict to ", 1)[1].split()[0])
        review_path.write_text("SUMMARY: checked\nSTATUS: PASS\n", encoding="utf-8")
        if False:
            yield None

    plan = tmp_path / "plan.md"
    plan.write_text("## Sprint Contract\n", encoding="utf-8")
    with patch("meow.agents.reviewer.query", side_effect=capture_review) as query:
        asyncio.run(reviewer.run_reviewer(sprint, plan))
        assert query.call_args.kwargs["options"].skills == [
            "superpowers:verification-before-completion"
        ]
        asyncio.run(reviewer.run_prompt_reviewer(sprint, "Request"))
        assert query.call_args.kwargs["options"].skills == [
            "superpowers:verification-before-completion"
        ]
    assert (tmp_path / "docs/exec-plans/active/plan-review.md").read_text(
        encoding="utf-8"
    ).startswith("SUMMARY: checked\nSTATUS: PASS")
    assert (tmp_path / "docs/exec-plans/active/review.md").read_text(
        encoding="utf-8"
    ).startswith("SUMMARY: checked\nSTATUS: PASS")
```

- [x] **Step 2: Run the new unittest module and confirm the expected failures**

Run: `.venv/Scripts/python -m unittest tests.test_agent_skills -v`

Expected: FAIL because no role currently assigns SDK skills and the planner prompt does not document the MEOW-specific constraints.

- [x] **Step 3: Assign the skills and constrain their use in the role prompts**

Set `skills` on the explorer `AgentDefinition`. Set `skills` on planner, generator, and both reviewer `ClaudeAgentOptions` objects. Explain in the planner prompt that `writing-plans` contributes task decomposition and self-review, while MEOW still owns the output path, Sprint Contract, and no-handoff behavior. Explain that explorer uses systematic debugging only for bug investigations and remains read-only. The generator uses `executing-plans` to work through the supplied plan one task at a time; adapt it to MEOW by leaving worktree setup, review rounds, and stopping control to the orchestrator, with no generator-owned ledger or commits. The generator uses TDD for code changes, systematic debugging for failures, `receiving-code-review` to verify feedback against the plan and code before fixing it, and verification-before-completion to report observed evidence. The reviewer uses verification-before-completion to independently check commands and code evidence, then writes the existing verdict format without editing implementation.

- [x] **Step 4: Document the mapping in README and compare it with the code**

Add a short “Agent skills” section that names each role, its exact plugin-qualified skills, and the behavior each skill guides. State that planner output remains a MEOW sprint plan and that the explorer remains read-only. Explain that the generator applies `executing-plans` only to task-by-task execution, leaving workspace setup and reviewer rounds to MEOW. Check every listed identifier against the role configuration and ensure no other skill names appear in this mapping.

- [x] **Step 5: Run the focused tests and the project suite**

Run: `.venv/Scripts/python -m unittest tests.test_agent_skills -v`

Expected: PASS for all five agent-entry-point checks.

Run: `.venv/Scripts/python -m unittest discover -s tests -v`

Expected: PASS for the complete suite, including the README/code mapping review above.

- [x] **Step 6: Commit the task**

```bash
git add src/meow/agents/explorer.py src/meow/agents/planner.py src/meow/agents/generator.py src/meow/agents/reviewer.py tests/test_agent_skills.py README.md
git commit -m "feat: assign superpowers skills to agents"
```

## Self-Review

- **Spec coverage:** The one task configures the six relevant installed skills across all four agent roles, keeps both reviewer paths covered, constrains `writing-plans` and `executing-plans` to MEOW's contract, and documents each assignment.
- **Placeholder scan:** No TBDs, TODOs, unspecified error handling, or unspecified test behavior remain.
- **Type consistency:** Explorer's `AgentDefinition.skills` and each main session's `ClaudeAgentOptions.skills` use the SDK's `list[str]` skill identifiers. The plan and review tests capture those exact option objects.
- **Review Focus:** Each of the five listed failure modes is checked in the task's tests or documentation comparison step.

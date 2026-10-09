---
name: customize
description: Customize MEOW by helping users choose whether to edit an agent, add an existing skill, create a skill, create an agent, or connect behavior to a specific MEOW workflow phase.
---

# Customize MEOW

Use this skill when a user wants to change how MEOW works, add capabilities to an agent, create a new agent or skill, or decide where custom behavior belongs in the MEOW workflow.

## Discover the requested customization

Start by asking concise questions when the request does not already answer them:

1. What should change?
   - edit an existing agent
   - add an existing skill to an agent
   - create a new skill
   - create a new agent
   - add or configure MCP/tool access
   - change workflow orchestration or prompts
2. Where should it apply?
   - native `/meow:*` execution
   - headless `meow` CLI / Agent SDK execution
   - both
3. Which workflow phase or role owns it?
   - setup/onboarding, exploration, planning, generation, review, fixing, testing, delivery, or another explicitly named phase
4. Which existing agent, skill, or command should be the base, if any?

Do not ask questions whose answers are already clear from the user's request. If a safe, narrow change is obvious, proceed and state the assumption.

## Explain the existing MEOW shape

Before adding a role or changing orchestration, inspect the relevant files and explain the closest existing components:

- **explorer:** read-only repository exploration, commonly exposed as a nested `AgentDefinition`.
- **planner:** creates plans and Sprint Contracts.
- **generator:** edits the worktree and participates in generator/reviewer rounds.
- **reviewer:** inspects changes and produces pass/fail findings.
- **tester:** runs configured validation and optional tester MCP servers.
- **review_fixer** and **lint_fixer:** narrowly scoped edit-and-verify roles.
- **issue_fetcher** and **gitlab_fetcher:** integration-specific read-only MCP roles.
- **docs_updater** and **setup:** specialized maintenance roles.

Use the smallest existing extension point that matches the request. Do not create a new agent when prompt guidance, a skill, a hook, an MCP configuration, or an existing role is sufficient.

## Route the customization

### Edit an existing agent

Inspect its implementation under `src/meow/agents/`, its prompt in `src/meow/project/prompts.py`, SDK `allowed_tools` and `skills`, context contracts, lifecycle, orchestration call sites, and permission policy. Preserve unrelated behavior and keep tool access minimal.

### Add an existing skill

For CLI mode, add the exact installed skill identifier to the target agent's `skills=[...]` in its `ClaudeAgentOptions`. For native mode, update the relevant instructions in `skills/_shared/native-mode.md` and the role prompt when needed. If the skill is missing, stop and offer to invoke `skill-creator`; do not invent, silently install, or substitute a skill.

### Create a skill

Offer to invoke `skill-creator` and clarify whether the skill should be available automatically or only when explicitly invoked. When the skill affects a MEOW role, integrate its native and CLI paths separately and verify its underlying tool/MCP permissions.

### Create an agent

Offer to base it on the closest existing role. Implement the role under `src/meow/agents/`, register its prompt and lifecycle, update the supported permission roles, add native/CLI dispatch where applicable, and write focused tests. A new Python class is not complete until the intended workflow can construct and reach it.

After understanding the proposed agent, ask whether the user also wants a dedicated skill for invoking or coordinating it. If yes, offer to invoke `skill-creator`. If they instead want to attach an existing skill, route through the add-existing-skill path above. Do not create or attach either silently.

### Add tools or MCP

Expose only the required SDK tools, configure MCP servers through the supported
project configuration, and inspect `.meow/config.toml`, the optional local
override, and the user fallback `~/.meow/config.toml` for permission rules. A
prompt or skill does not grant tool,
filesystem, shell, network, or MCP access. Preserve existing allow/ask/deny
boundaries unless the user explicitly requests a permission change.

## Native and CLI parity

When the user selects both modes, make the behavior explicit in both places:

- CLI/Agent SDK: agent options in `src/meow/agents/` and any configured MCP servers.
- Native `/meow:*`: the relevant wrapper skill, `skills/_shared/native-mode.md`, and role prompts in `src/meow/project/prompts.py`.

Do not claim parity if only one path was changed.

## Verify and report

Run focused tests for changed agent construction, prompt generation, permissions, and workflow dispatch. Run `meow native verify` when native integration or configured MCP readiness is relevant; readiness alone does not prove a real MCP call. Report:

- what customization was selected;
- where it was connected in the MEOW flow;
- which agents, skills, tools, and MCP servers changed;
- any permission or installation step still required; and
- what verification actually ran.

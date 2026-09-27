# Handoff: Split the Harness into Its Own Repo

**For the Claude Code session executing this:** you have local filesystem and terminal access, which is why this is being handed to you rather than done remotely. Work through this in order. Where a step says "ask the user," stop and ask — don't guess. Verify each phase by actually running the command shown, not by reading the code and assuming it works.

## What's changing and why

Everything so far (`harness_orchestrator.py`, the implementation checklist) assumed the harness script lived inside the project it operates on, with values like the lint command hardcoded into the script itself. That's fine for one project, but doesn't reuse across repos and doesn't version independently from the code it's harnessing.

This handoff splits that into two things:
- **A separate, installable harness repo** — the generic engine (`explorer`/`planner`/`generator`/`evaluator` + the loop), with nothing project-specific baked in.
- **A small addition to each project repo** that uses it — a `.harness.toml` config file plus a pointer in `AGENTS.md`. No project-specific code lives in the harness anymore.

Two files come with this handoff:
| File | Goes where | Purpose |
|---|---|---|
| `orchestrator.py` (+ `pyproject.toml`) | New harness repo | The engine itself — now reads project config instead of hardcoding it |
| `.harness.toml` | Each project repo that uses the harness (e.g. `my-app/`) | Per-project settings the engine reads at runtime |

---

## Phase 1 — Create the harness repo

- [ ] **Ask the user** where the new repo should live (e.g. `~/code/harness-engine`) and what to name it.
- [ ] Create the directory and `git init` it.
- [ ] Lay out the package:
  ```
  harness-engine/
  ├── pyproject.toml
  ├── README.md
  └── src/
      └── harness_engine/
          ├── __init__.py
          └── orchestrator.py     # the provided file
  ```
- [ ] Place the provided `orchestrator.py` at `src/harness_engine/orchestrator.py`.
- [ ] Place the provided `pyproject.toml` at the repo root, adjusting the `name`, `authors`, and `description` fields to fit — don't leave placeholder values in.
- [ ] Create an empty `src/harness_engine/__init__.py`.
- [ ] Write a short `README.md`: what this is, the one-line install command, the one-line run command, and a pointer to `.harness.toml`'s required fields (copy the field list from the provided `.harness.toml`'s comments).
- [ ] Commit.

## Phase 2 — Install it

- [ ] From the harness repo: `pip install -e .` (editable install, so local changes take effect immediately — ask the user if they'd rather install into a dedicated virtualenv rather than their global/system Python).
- [ ] Verify the console script is on PATH: `harness --help` should run without error.
- [ ] **Ask the user:** do they want this published somewhere (private PyPI index, git-installable via `pip install git+...`) so other machines/teammates can install it without a local path, or is local editable install sufficient for now? If they want git-installable, confirm the repo has been pushed somewhere reachable and note the install command in the README.

## Phase 3 — Wire up a project to use it

- [ ] In the target project repo (e.g. `my-app/`), place the provided `.harness.toml` at the repo root.
- [ ] Fill in its real values — **ask the user** for the actual lint command if it's not already recorded from earlier setup, and for which models each role should run on (or leave the model fields absent to use the engine's defaults).
- [ ] Update `AGENTS.md` at the project root (create it if it doesn't exist) to say sprint work runs via the installed `harness` command, not a local script — something like:
  ```markdown
  ## Harness

  Feature work in this repo runs through the harness engine, not ad hoc
  editing. From the repo root: `harness run "<feature description>"`.
  Config lives in `.harness.toml`. See <harness repo link> for the engine
  itself.
  ```
- [ ] Confirm `docs/exec-plans/active/` still exists in the project repo (from earlier setup) — the harness writes sprint files there regardless of which repo the engine code lives in.

## Phase 4 — Verify end to end

- [ ] From inside the project repo: `harness run "<trivial test feature>"`.
- [ ] Confirm it reads `.harness.toml` correctly — check the lint command and models it reports using match what you put in the config, not any old hardcoded defaults.
- [ ] Confirm sprint files land in the **project repo's** `docs/exec-plans/active/`, not the harness repo.
- [ ] Confirm the full loop still works: planner writes a contract, generator implements, evaluator grades, loop resolves to PASS or hits `max_rounds` from config.
- [ ] **Ask the user:** does everything here match what they expected, or do they want the harness repo published/pinned to a version before relying on it for real work?

Report back: where the harness repo ended up, the exact install/run commands, and confirmation the end-to-end test passed.

# MEOW

Claude Agent SDK harness (`meow`) and Claude Code plugin (`skills/`).

## Development

From the repo root with `.venv` active (`python -m pip install -e ".[dev]"`):

```bash
python -m pytest -q
ruff check .
ruff format --check .
```

Use pytest for both unittest and function tests. Root imports use the installed
`src/` package; linked worktrees need `PYTHONPATH=src`.

## Context and references

Read only the relevant guide/section. Keep maintained docs/skills below 200 lines;
remove repetition and link topic-specific details instead of loading everything.
Reduce words too; do not meet the line limit by minifying prose.

- [CLI](docs/CLI.md): command index; `meow COMMAND --help` gives syntax.
- [Integrations](docs/INTEGRATIONS.md): configuration and optional services.
- [Workflows](docs/WORKFLOWS.md): definitions, scopes, templates, recovery.
- [Architecture](docs/ARCHITECTURE.md): package/module ownership.
- [Native protocol](skills/_shared/native-mode.md): in-session helpers.

Config priority: `.meow/config.local.toml` > `.meow/config.toml` >
`~/.meow/config.toml`. Runtime evidence/journals live in `.meow/`, plans default
to `.meow/plans/`, and project docs stay in `docs/`.

## Ownership

Use the narrowest owning package; package roots contain entry points.
Role prose belongs in `src/meow/project/custom/builtin/agents/*.toml`;
`project/prompts.py` owns computed context/verdict contracts.

`skills/*/SKILL.md` owns plugin flows. Run defaults to CLI;
plan/review/lint default to native. Orchestrator tasks
remain detached CLI sprints. Schedule uses OS/CLI; onboard adds optional setup,
migration converts legacy layouts, and customize edits definitions.
Keep native/CLI guidance aligned.

## Invariants

- SDK `allowed_tools` bypasses `can_use_tool`; enforce with `agents.base.guard_tools`.
- Verdicts start `SUMMARY:`, then `STATUS: PASS|FAIL`; parse only with
  `agents.reviewer._verdict_status`.
- Use `project/plan_files.py` helpers `plan_review_file`, `plan_test_file`,
  `new_review_filename`; never reconstruct artifact names.
- CLI/native orchestration share `execution/orchestration/tick.py`.
  Push only in delivering; task refs use `orchestrator/<id>-<task>`,
  never `orchestrator/<id>/<task>`, beside integration `orchestrator/<id>`.
- `run_sprint` marks pre-terminal exceptions failed; inner steps record phases only.
- Definitions replace whole files: local > project > user > builtin.
  Gates/permissions remain code-owned.
- Claude owns transcripts; journals store role/session IDs, resumed only by `meow resume`.

# meow

The harness engine: a planner/generator/reviewer loop built on the
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

`meow` is *generic* — nothing about any particular project is baked into it.
Four agents (`explorer`, `planner`, `generator`, `reviewer`) are coordinated by
plain Python control flow: the planner writes a sprint plan with a testable
Sprint Contract, the generator implements it under an auto-fixing lint hook, and
a skeptical reviewer grades the result PASS/FAIL. FAIL feeds back into the
generator, up to `max_rounds` times.

Every project-specific value — the lint commands, the per-role models, the round
cap, where sprint files land — is read at runtime from a `.harness.toml` file in
the *target project's* root. This repo holds only the engine.

## Install

Into a dedicated virtualenv, from this repo's root:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e .
```

(`.venv/bin/python` on macOS/Linux.) The install is editable, so edits to the
engine take effect immediately with no reinstall. Verify with
`.venv/Scripts/meow --help`.

Because the venv is local to this repo, `meow` is *not* on your global PATH.
From another project's root, either activate the venv first or call the script
by its full path:

```bash
/path/to/meow/.venv/Scripts/meow run "Add CSV export"
```

If you'd rather have `meow` available everywhere without activating anything,
`pipx install -e .` gives it its own environment but a global shim.

## Run

From the root of a project that has a `.harness.toml` (with the venv active,
or via the full path shown above):

```bash
meow run "Add CSV export"
```

Pass `--project-root PATH` to run against a project other than the current
directory. The sprint plan and review land in that project's `docs_dir`, never
in this repo.

Two narrower subcommands are also available:

```bash
meow plan "Add CSV export"         # write the sprint plan only, don't implement it
meow review                        # re-review + fix the latest plan in docs_dir
meow review --plan-file PATH       # re-review + fix a specific plan
```

`review` runs the reviewer first; if it already passes, nothing else runs. On
FAIL it loops the generator against the feedback and re-reviews, same as `run`,
up to `max_rounds`.

## Claude Code plugin

This repo doubles as a Claude Code plugin: add it as a plugin source and three
skills become available in any project that also has a `.harness.toml`:

| Skill | Equivalent to |
|---|---|
| `/meow:sprint "<feature>"` | `meow run "<feature>"` |
| `/meow:meow-plan "<feature>"` | `meow plan "<feature>"` |
| `/meow:meow-review` | `meow review` |

Each skill is a thin wrapper — see `skills/*/SKILL.md` — that shells out to the
same `meow` CLI, so it needs `meow` importable the same way (venv active,
or a `pipx install -e .`/global install).

## Onboarding a project

Copy a template from `templates/` to the target repo's root as
`.harness.toml`: `harness.toml.example` (annotated, language-neutral),
`harness.toml.python.example`, or `harness.toml.typescript.example`.

See [GUIDE.md](GUIDE.md) for the full field reference, the recommended `docs/`
layout, and a setup checklist — it's written for a Claude session onboarding a
different repo onto meow, generic to any language.

## Layout

```
meow/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── GUIDE.md                             # onboarding a *different* repo onto meow
├── .claude-plugin/
│   └── plugin.json                      # Claude Code plugin manifest
├── skills/
│   ├── sprint/SKILL.md                  # /meow:sprint  -> harness run
│   ├── meow-plan/SKILL.md               # /meow:meow-plan   -> harness plan
│   └── meow-review/SKILL.md             # /meow:meow-review -> harness review
├── docs/
│   └── exec-plans/
│       └── active/                      # meow harnessing itself writes here
├── templates/
│   ├── harness.toml.example             # annotated, language-neutral
│   ├── harness.toml.python.example      # ruff (gate) + mypy (advisory)
│   └── harness.toml.typescript.example  # eslint (gate) + tsc (advisory)
└── src/
    └── meow/
        ├── __init__.py
        ├── config.py                   # .harness.toml loading + lint-command model
        ├── sprint.py                   # per-sprint state shared by every role
        ├── lint.py                     # auto-fixing per-file lint hook
        ├── roles.py                    # explorer/planner/generator/reviewer agents
        ├── orchestrator.py             # generator <-> reviewer round loop
        └── cli.py                      # `harness` console-script entry point
```

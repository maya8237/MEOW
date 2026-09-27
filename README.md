# meow

The harness engine: a planner/generator/evaluator loop built on the
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

`meow` is *generic* — nothing about any particular project is baked into it.
Four agents (`explorer`, `planner`, `generator`, `evaluator`) are coordinated by
plain Python control flow: the planner writes a sprint plan with a testable
Sprint Contract, the generator implements it under an auto-fixing lint hook, and
a skeptical evaluator grades the result PASS/FAIL. FAIL feeds back into the
generator, up to `max_rounds` times.

Every project-specific value — the lint command, the per-role models, the round
cap, where sprint files land — is read at runtime from a `.harness.toml` file in
the *target project's* root. This repo holds only the engine.

## Install

Into a dedicated virtualenv, from this repo's root:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e .
```

(`.venv/bin/python` on macOS/Linux.) The install is editable, so edits to the
engine take effect immediately with no reinstall. Verify with
`.venv/Scripts/harness --help`.

Because the venv is local to this repo, `harness` is *not* on your global PATH.
From another project's root, either activate the venv first or call the script
by its full path:

```bash
/path/to/meow/.venv/Scripts/harness run "Add CSV export"
```

If you'd rather have `harness` available everywhere without activating
anything, `pipx install -e .` gives it its own environment but a global shim.

## Run

From the root of a project that has a `.harness.toml` (with the venv active,
or via the full path shown above):

```bash
harness run "Add CSV export"
```

Pass `--project-root PATH` to run against a project other than the current
directory. The sprint plan and review land in that project's `docs_dir`, never
in this repo.

## Configuring a project to use it

Copy [`templates/harness.toml.example`](templates/harness.toml.example) to the
project root as `.harness.toml` and fill it in. Fields:

| Field | Required | Default | Purpose |
|---|---|---|---|
| `lint_command` | **yes** | — | The project's lint/check command, **without** any fix flag (e.g. `ruff check`, `eslint`, `golangci-lint run`). Run unmodified as the evaluator's full-project gate. |
| `lint_fix_flag` | no | `--fix` | Appended to `lint_command` for the auto-fixing per-file hook on every file the generator writes. |
| `max_rounds` | no | `8` | Generator↔evaluator rounds allowed before the sprint gives up rather than looping forever. |
| `docs_dir` | no | `docs/exec-plans/active` | Where sprint plan/contract/review files are written, relative to the project root. Must already exist. |
| `[models]` | no | see below | Per-role model overrides: `explorer`, `planner`, `generator`, `evaluator`. Omit a key to use the engine's default for that role. |

The engine defaults `explorer` to `haiku` (cheap, read-only research) and leaves
the other three to the SDK's own default model.

It's also worth pointing the project's `AGENTS.md` at the harness, so feature
work runs through it rather than ad hoc editing:

```markdown
## Harness

Feature work in this repo runs through the harness engine, not ad hoc
editing. From the repo root: `harness run "<feature description>"`.
Config lives in `.harness.toml`. See <meow repo link> for the engine itself.
```

## Layout

```
meow/
├── pyproject.toml
├── README.md
├── docs/
│   └── harness-split-repo-handoff.md   # why the engine lives in its own repo
├── templates/
│   └── harness.toml.example            # copy into consuming projects
└── src/
    └── meow/
        ├── __init__.py
        └── orchestrator.py             # the engine
```

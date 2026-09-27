# meow

The harness engine: a planner/generator/evaluator loop built on the
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).

`meow` is *generic* — nothing about any particular project is baked into it.
Four agents (`explorer`, `planner`, `generator`, `evaluator`) are coordinated by
plain Python control flow: the planner writes a sprint plan with a testable
Sprint Contract, the generator implements it under an auto-fixing lint hook, and
a skeptical evaluator grades the result PASS/FAIL. FAIL feeds back into the
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
project root as `.harness.toml` and fill it in. Top-level fields:

| Field | Required | Default | Purpose |
|---|---|---|---|
| `[[lint]]` | **yes** | — | One or more lint commands. See below. |
| `max_rounds` | no | `8` | Generator↔evaluator rounds allowed before the sprint gives up rather than looping forever. |
| `docs_dir` | no | `docs/exec-plans/active` | Where sprint plan/contract/review files are written, relative to the project root. Must already exist. |
| `[models]` | no | see below | Per-role model overrides: `explorer`, `planner`, `generator`, `evaluator`. Omit a key to use the engine's default for that role. |

The engine defaults `explorer` to `haiku` (cheap, read-only research) and leaves
the other three to the SDK's own default model.

> **TOML ordering.** Every top-level key must appear *above* the first
> `[[lint]]` table — anything after one belongs to that table, not to the file.
> The engine rejects a misplaced key with an error naming it.

### Lint commands

A project declares any number of lint commands, one `[[lint]]` table each, run
in the order listed:

| Key | Required | Default | Purpose |
|---|---|---|---|
| `command` | **yes** | — | The check-only command, **without** any fix flag (e.g. `ruff check`, `npx oxlint`, `golangci-lint run`). |
| `fix_flag` | no | none | Appended when linting a single file, so the per-file hook can auto-fix. Omit for a command with no fix mode. |
| `per_file` | no | `true` | Run as a hook on every file the generator writes. Set `false` for whole-project analysis that means nothing pointed at one file. |
| `gate` | no | `true` | A project-wide failure is a sprint FAIL. Set `false` to make a command advisory — what you want for an analyzer that reports findings on pre-existing code. |

```toml
# The gate: per-file and auto-fixing, and its failure fails the sprint.
[[lint]]
command = "npx oxlint"
fix_flag = "--fix"

# Advisory project-graph analysis: informs the review, cannot fail it.
[[lint]]
command = "npx fallow"
per_file = false
gate = false
```

Each command's program is resolved on `PATH` before running, so `npx ...` works
on Windows, where the shim is a `.cmd` file that cannot be exec'd from a bare
argv.

The older single-command form still works and is equivalent to one `[[lint]]`
entry that is both per-file and a gate:

```toml
lint_command = "ruff check"
lint_fix_flag = "--fix"
```

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

# AGENTS.md

## Harness

Feature work in this repo runs through the harness engine — which is this
repo — not ad hoc editing. From the repo root, with `.venv` active:

```bash
harness run "<feature description>"
```

Config lives in `.harness.toml`. Sprint plans, contracts, and reviews are
written to `docs/exec-plans/active/`.

## Lint

`ruff check` is the gate, declared as the single `[[lint]]` entry in
`.harness.toml`. The harness appends `--fix` when linting individual files the
generator touches, and runs `ruff check` unmodified project-wide as part of the
evaluator's verdict. Rule selection lives in `pyproject.toml`.

A project can declare any number of `[[lint]]` commands, each choosing whether
it runs per file and whether its failure may fail a sprint; meow itself only
needs one.

## Layout

The engine is a single module, `src/meow/orchestrator.py`. The `src/` layout is
deliberate: code run from the repo root reaches the *installed* copy, so a
broken editable install is caught rather than masked.

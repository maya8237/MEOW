# GUIDE.md — Initializing meow in a Project

For a Claude session (or anyone else) working in a **different** repo that
wants feature work to run through **meow** instead of ad hoc editing. meow
itself is language-agnostic — the only per-project choice is your lint
command(s).

---

## 1. Required

meow fails immediately, with a clear error, before any agent call is made, if
any of these are missing:

- **meow installed and reachable** — see this repo's README for the venv/pipx
  install. Verify with `meow --help`; if it's a venv-local install, either
  activate it or call it by full path.
- **`.harness.toml` at the project root**, with at least one `[[lint]]` entry
  (or the legacy `lint_command`). Missing file → `FileNotFoundError`; zero
  lint commands → `ValueError`. See §2 for the fields, or copy a starting
  point from `templates/`: `harness.toml.example` (annotated, language-neutral),
  `harness.toml.python.example` (ruff + mypy), `harness.toml.typescript.example`
  (eslint + tsc).
- **`ANTHROPIC_API_KEY`** (or your provider's equivalent) in the environment —
  without it, agent calls fail at the SDK level, not with a meow error.
- **A writable `docs_dir`** — doesn't need to pre-exist (`run_planner`
  `mkdir`s it), just needs to resolve inside the project root.
- **Your lint command actually working** — run it by hand first. If it's
  broken or unconfigured, the per-file hook and the reviewer's gate both fail
  silently useless.

---

## 2. `.harness.toml` fields

Every top-level key must appear **above** the first `[[lint]]` table — TOML
attaches anything after it to that table, and meow rejects the resulting
unknown key by name rather than ignoring it.

| Field | Required | Default | Notes |
|---|---|---|---|
| `[[lint]]` | **Yes**, ≥1 | — | See below. |
| `max_rounds` | No | `8` | Generator↔reviewer rounds before meow gives up. |
| `docs_dir` | No | `docs/exec-plans/active` | Relative to project root; auto-created. |
| `[models].explorer` | No | `"haiku"` | Cheap, read-only research role. |
| `[models].{planner,generator,reviewer}` | No | SDK default | `generator` does the heaviest work — consider pinning it explicitly. |

Each `[[lint]]` table (run in listed order, program resolved on `PATH` so
`npx ...` works on Windows):

| Key | Required | Default | Purpose |
|---|---|---|---|
| `command` | **Yes** | — | Check-only, no fix flag, e.g. `ruff check`, `npx oxlint`, `golangci-lint run`. |
| `fix_flag` | No | none | Appended only for the per-file auto-fix hook. |
| `per_file` | No | `true` | `false` for whole-project-only analysis. |
| `gate` | No | `true` | `false` makes it advisory — reported, never fails the sprint. |

```toml
[[lint]]                 # gate: per-file, auto-fixing, fails the sprint
command = "npx oxlint"
fix_flag = "--fix"

[[lint]]                 # advisory: project-wide only, never fails the sprint
command = "npx fallow"
per_file = false
gate = false
```

---

## 3. Recommended, not enforced

Nothing in `orchestrator.py` checks for these — they help only if the
`explorer` subagent happens to find them while researching a feature request.
For a guaranteed read, name the doc directly in the request (e.g.
`harness run "Add CSV export -- see docs/product-specs/reports.md"`).

```
docs/
├── ARCHITECTURE.md          # see below
├── design-docs/core-beliefs.md
├── exec-plans/{active,completed}/, tech-debt-tracker.md
├── product-specs/
└── references/
```

**`docs/ARCHITECTURE.md`** is the highest-value one: the reviewer runs a
SOLID/SRP pass (`_architecture_review_instructions`) that fails any file
mixing unrelated responsibilities. Without a stated architecture it's
guessing at what "one responsibility" means for *this* project. The rest
(`completed/`, `tech-debt-tracker.md`, `core-beliefs.md`) are never written or
read automatically — just useful conventions for humans and agents sharing
the repo.

---

## 4. `AGENTS.md`

Purely a human/agent-facing convention — not read by `orchestrator.py`:

```markdown
## Harness

Feature work in this repo runs through meow, not ad hoc editing. From the repo
root: `meow run "<feature description>"`.
Config lives in `.harness.toml`. See <meow repo link>'s GUIDE.md for setup.
```

---

## 5. Setup checklist

- [ ] `meow --help` runs
- [ ] `ANTHROPIC_API_KEY` set
- [ ] `.harness.toml` has ≥1 `[[lint]]` entry, all top-level keys above it
- [ ] Each lint `command` works run by hand
- [ ] `meow run "<trivial test feature>"` — reported lint/models match
      *your* config (not defaults), a plan + `-review.md` land in `docs_dir`,
      and it resolves to `STATUS: PASS` or a clean `max_rounds` error

---

## 6. Sharing across a team

A local editable install is enough for one person. Beyond that: `pip install
git+<meow-repo-url>` (no local path needed) or a private package index —
either way, pin to a tag/commit once others depend on it, rather than a
moving branch.

---

## 7. If something's missing

| Missing / wrong | Result |
|---|---|
| `.harness.toml` | `FileNotFoundError` before any agent runs |
| No `[[lint]]` entries or `lint_command` | `ValueError`: no lint command defined |
| Unknown key in a `[[lint]]` table (often a top-level key placed after it) | `ValueError` naming the entry and key |
| `ANTHROPIC_API_KEY` | API-level auth error on first agent call |
| `docs/ARCHITECTURE.md` etc. | No error — less context, weaker SRP review |
| `AGENTS.md` | No effect on meow — human-facing only |

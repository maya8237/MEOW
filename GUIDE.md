# GUIDE.md — Initializing MEOW in a Project

For a Claude session (or anyone else) working in a **different** repo that
wants feature work to run through **MEOW** — Management, Execution &
Optimization of Workflows — instead of ad hoc editing. MEOW itself is
language-agnostic — the only per-project choice is your lint command(s).

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

[[lint]]                 # non-blocking: project-wide only, never fails the sprint
command = "npx fallow"
per_file = false
gate = false
```

---

## 3. Recommended, not enforced — but do it anyway

No role is *required* to have any of these present — meow runs fine without
them. When project documentation should guide a role, name it in the request
(for example, `meow run "Add CSV export -- see docs/product-specs/reports.md"`).

```
docs/
├── ARCHITECTURE.md          # see below — write this one first
├── design-docs/core-beliefs.md
├── exec-plans/{active,completed}/, tech-debt-tracker.md
├── product-specs/
└── references/
```

**Some file describing the architecture** (conventionally
`docs/ARCHITECTURE.md`, but any role's docs scan will pick it up under
whatever name or location it actually has) is the highest-value one to write
during onboarding, not defer: on every sprint, the reviewer's SOLID/SRP pass
looks for it and fails the sprint on violations of the module boundaries and
dependency rules it states, on top of its generic mixed-responsibility check.
Without it, that pass has nothing project-specific to check against — meow
will still run, but every architecture review is a coin flip instead of a
check against your actual design. The gap compounds as a project grows: on a
five-file repo the explorer can infer the shape by reading everything, but on
a real codebase with several packages/modules it can't, and an unstated
architecture produces reviews that are inconsistent from one sprint to the
next, or that enforce a structure nobody actually chose. Write it when you
onboard a project, before the first `meow run`, not after the review
quality suffers — it doesn't need to be long, just state the module
boundaries and who's allowed to depend on whom, and it doesn't need the name
`ARCHITECTURE.md` specifically, just to live somewhere under `docs/`.

The rest (`completed/`, `tech-debt-tracker.md`, `core-beliefs.md`) get the
same opportunistic treatment as everything else in `docs/`: no role goes
looking for them specifically, but any role's docs scan picks them up when
they're relevant to what it's doing, and a human sharing the repo reads them
directly. Still worth setting up for the same reason: cheap now, and each one
is a substitute for context a role would otherwise have to re-derive by
exploring the whole repo every time it happens to matter.

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
- [ ] A doc describing the project's architecture written somewhere under
      `docs/` (conventionally `docs/ARCHITECTURE.md`) — not enforced by meow,
      but skipping it on anything beyond a toy project degrades every
      architecture review from here on; see §3
- [ ] `meow run "<trivial test feature>" --name "trivial-test-feature"` — reported lint/models match
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
| No architecture doc anywhere under `docs/` | No error — reviewer's SOLID/SRP pass finds nothing to Glob/Read, so it has no project-specific boundaries to check, just its generic mixed-responsibility rule |
| Other `docs/` files (`tech-debt-tracker.md`, `core-beliefs.md`, etc.) | No error — no role goes looking for them specifically, only opportunistically via each role's docs scan |
| `AGENTS.md` | No effect on meow — human-facing only |

# GUIDE.md — Initializing MEOW in a Project

For a Claude session (or anyone else) working in a **different** repo that
wants feature work to run through **MEOW** — Management, Execution &
Optimization of Workflows — instead of ad hoc editing. MEOW itself is
language-agnostic — the only per-project choice is your lint command(s).

Jira/GitLab integration, scheduled unattended runs, and a full
error-message reference live in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
Skills and native-vs-CLI execution mode are covered in the main repo's
[README.md](README.md).

## 1. Required

meow fails immediately, with a clear error, before any agent call is made, if
any of these are missing:

- **meow installed and reachable** — see this repo's README for the venv/pipx
  install. Verify with `meow --help`; activate the venv or call it by full path.
- **`.harness.toml` at the project root**, with at least one `[[lint]]` entry
  (or the legacy `lint_command`). Missing file → `FileNotFoundError`; zero
  lint commands → `ValueError`. See §2, or copy a starting point from
  `templates/`: `harness.toml.example` (annotated, language-neutral),
  `harness.toml.python.example` (ruff + mypy), `harness.toml.typescript.example`
  (eslint + tsc).
- **A writable `docs_dir`** — doesn't need to pre-exist, just needs to resolve
  inside the project root.
- **Your lint command actually working** — run it by hand first. If it's
  broken or unconfigured, the per-file hook and the reviewer's gate both fail
  silently useless.

## 2. `.harness.toml` fields

Every top-level key must appear **above** the first `[[lint]]` table — TOML
attaches anything after it to that table, and meow rejects the resulting
unknown key by name rather than ignoring it.

| Field | Required | Default | Notes |
|---|---|---|---|
| `[[lint]]` | **Yes**, ≥1 | — | See below. |
| `max_rounds` | No | `8` | Generator↔reviewer rounds before meow gives up. |
| `docs_dir` | No | `docs/exec-plans/active` | Relative to project root; auto-created. |
| `lint_timeout` | No | `60` | Seconds before a per-file lint command is killed. |
| `[models].explorer` | No | `"haiku"` | Cheap, read-only research role. |
| `[models].{planner,generator,reviewer}` | No | SDK default | `generator` does the heaviest work — consider pinning it explicitly. |

Each `[[lint]]` table (run in listed order, program resolved on `PATH` so
`npx ...` works on Windows):

| Key | Required | Default | Purpose |
|---|---|---|---|
| `command` | **Yes** | — | Check-only, no fix flag, e.g. `ruff check`, `npx oxlint`, `golangci-lint run`. |
| `fix_flag` | No | none | Appended for the per-file auto-fix hook, and for `meow lint-fix`'s project-wide auto-fix pass. Commands with no `fix_flag` are checked but never auto-fixed by either. |
| `per_file` | No | `true` | `false` for whole-project-only analysis. |
| `gate` | No | `true` | `false` makes it advisory — reported, never fails the sprint. |

A second, non-blocking `[[lint]]` entry (`gate = false`, `per_file = false`)
is how a whole-project-only analyzer reports findings without failing the
sprint — see `templates/harness.toml.example` for a worked example.

`[jira]`/`[jira.mcp]` (for `meow issue`) and `[gitlab.mcp]` (for `meow
gitlab-review`) are optional — see
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for their fields and a security
note on `[gitlab.mcp].env`.

## 3. Recommended, not enforced — but do it anyway

No role is *required* to have any of these present — meow runs fine without
them. When project documentation should guide a role, name it in the request
(for example, `meow run "Add CSV export -- see docs/product-specs/reports.md"`).

**Some file describing the architecture** (conventionally
`docs/ARCHITECTURE.md`, picked up under any name/location by a role's docs
scan) is the highest-value one to write during onboarding: on every sprint,
the reviewer's SOLID/SRP pass looks for it and fails the sprint on violations
of the module boundaries and dependency rules it states. Without it, every
architecture review is a coin flip instead of a check against your actual
design. It doesn't need to be long — just state the module boundaries and
who's allowed to depend on whom.

**`docs/RULES.md`** is read directly by meow and injected into every role's
system prompt, scoped by an optional `## Reviewer`/`## Planner`/`## Generator`/
`## Explorer` heading — see the README's "Project rules" section for the
format and an example. No `docs/RULES.md` at all is the default and needs no
setup.

## 4. `AGENTS.md`

Purely a human/agent-facing convention — not read by meow itself. Include
enough that a session can go from "nothing set up" to a running sprint
without leaving this file: the run command, where `meow` comes from (this
destination repo gets its own venv — it does not share meow's), how to
(re)create that venv if broken, and that no separate credential setup is
needed:

```markdown
## Harness

Feature work in this repo runs through meow, not ad hoc editing. From the repo
root, with this repo's own `.venv` active (or by full path,
`.venv/Scripts/meow` on Windows / `.venv/bin/meow` elsewhere):

    meow run "<feature description>" --name "<feature-name>"

The engine lives in <meow repo path>, installed into *this* repo's `.venv` as
an editable package. It shells out to the `claude` CLI via the Claude Agent
SDK and relies on that CLI's own authentication — no `ANTHROPIC_API_KEY`
needed. If `.venv` is missing or `meow --help` fails, recreate it:

    python -m venv .venv
    .venv/Scripts/python -m pip install -e "<meow repo path>"

Config lives in `.harness.toml`. See <meow repo link>'s GUIDE.md for setup.
```

## 5. Setup checklist

- [ ] `meow --help` runs
- [ ] `claude` CLI is installed and already authenticated (meow shells out to
      it via the Claude Agent SDK — no `ANTHROPIC_API_KEY` or other credential
      setup needed; verify with `claude --version` or `claude doctor`)
- [ ] `.harness.toml` has ≥1 `[[lint]]` entry, all top-level keys above it
- [ ] Each lint `command` works run by hand
- [ ] A doc describing the project's architecture written somewhere under
      `docs/` (conventionally `docs/ARCHITECTURE.md`) — not enforced by meow,
      but skipping it on anything beyond a toy project degrades every
      architecture review from here on; see §3
- [ ] `meow run "<trivial test feature>" --name "trivial-test-feature"` — reported lint/models match
      *your* config (not defaults), a plan + `-review.md` land in `docs_dir`,
      and it resolves to `STATUS: PASS` or a clean `max_rounds` error

## 6. Sharing across a team

A local editable install is enough for one person. Beyond that: `pip install
git+<meow-repo-url>` (no local path needed) or a private package index —
either way, pin to a tag/commit once others depend on it, rather than a
moving branch.

## 7. Troubleshooting

A missing `.harness.toml` or `[[lint]]` entry fails fast with
`FileNotFoundError`/`ValueError` before any agent runs; an unknown key in a
`[[lint]]` table (often a top-level key placed after it) raises `ValueError`
naming the entry and key; a missing architecture doc under `docs/` is not an
error, it just leaves the reviewer's SOLID/SRP pass with nothing
project-specific to check against. `meow issue`, `meow gitlab-review`, and
`meow review-fix-review` have their own failure modes (missing MCP config,
unreachable server, no MR checkout, `max_rounds` exhausted, etc.) — the full
error-message reference lives in
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

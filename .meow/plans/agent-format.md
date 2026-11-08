# Agent file format: TOML vs Claude Code Markdown

Research and plan only; based on `claude/awesome-mendel-btcxyb` (kits). Touches no
`kit*.py`, `tests/kits/`, `docs/KITS.md`, or `kits_cli.py` until the kits work lands.

## Recommendation

Keep TOML as the canonical format for agents that MEOW workflows run. Do not
convert the builtins. Make **publishing**, not the source format, the portable
step: MEOW generates sanitized `.md` subagents from any definition marked
portable, the same way `flat_skills.py` publishes skills today. `.md` stays a
first-class input, gains the flat MEOW keys, and is never more than a subset.

That is option D with A's generator, and it changes D in three ways:

1. **Publish by generating, never by copying.** Copying a kit's `.md` would pass
   `hooks`, `mcpServers`, and `permissionMode` straight to Claude Code. A
   generator writes only allowlisted keys, so the allowlist is enforced in code
   (AGENTS.md: permissions stay code-owned).
2. **A full "equal" `.md` is not worth it.** Variants and fragments are maps of
   multi-line text. In frontmatter they need real YAML (B) or a sidecar (C).
   Agents that need them are workflow agents. Their prompts depend on run
   context and are useless standalone, so a Markdown form buys nothing.
3. **Builtins ship as plugin agents.** Builtins are not synced into every
   project. Portable builtins are pre-rendered into the MEOW plugin's `agents/`
   folder (`meow:explorer`). Claude Code itself ignores `hooks`, `mcpServers`, and
   `permissionMode` for plugin agents. Names are namespaced, so they never
   collide with the user's own agents.

Your lean ("converting everything loses features") is right. Where D goes wrong
is making `.md` the publishing path. Copying `.md` is the unsafe path, and
rendering is what makes an agent portable.

## Options

| | Source of truth | Variants / fragments | session, lint_on_edit, agents | Placeholders standalone | Back-compat | Security of published agent | Cost |
|---|---|---|---|---|---|---|---|
| A TOML + generated `.md` | TOML | TOML | TOML | rendered at publish | full | allowlist in generator | low |
| B `.md` + rich frontmatter | `.md` | YAML maps (needs PyYAML or a bigger parser) | frontmatter | literal unless rendered | dual loader forever | copy leaks unknown future keys | high |
| C `.md` body + sibling TOML | both | sidecar TOML | sidecar TOML | literal unless rendered | dual loader; two files per agent drift apart | same as B | medium-high |
| D status quo + `.md` equal + publish `.md` | either | TOML only | TOML only (or lost) | literal | full | copy is unsafe unless filtered | medium |
| **E (rec.) A + flat MEOW keys in `.md` + plugin builtins** | either; TOML for workflow agents | TOML only (`.md` with them is rejected) | TOML keys; `.md` uses `meow-session`, `meow-lint-on-edit`, `meow-agents` | rendered with a standalone context; publishing fails on any unresolved placeholder | full | generator allowlist; plugin strips risky fields | medium |

## Answers

**1. Where MEOW-only settings live (option E).** In TOML, nothing changes.
Variants and fragments are TOML only, so a `.md` that needs them is told to use
TOML. `session`, `lint_on_edit`, and delegates become `meow-*` frontmatter keys.
They are flat scalars and lists the current `frontmatter.py` already parses, and
Claude Code ignores them. The `meow-` prefix avoids clashes with fields Claude
Code adds later (a future `agents` or `session` key, for example).

**2. Placeholders.** MEOW renders the body in its own runs, as it does today.
Publishing renders with a **standalone context**:
- `active_dir` becomes "the current working directory".
- `{{#if}}` values that are not set are false.
- Fragments are inlined.
- An optional `[variants] standalone` prompt wins when present.
- Any other variable that is still needed (`plan_file`, `verdict_format`,
  `docs_dir`...) is a publish error, and `meow agents validate` reports it.

Builtins:
- **explorer**: worth building. One placeholder; good as a read-only Haiku
  researcher.
- **reviewer**: needs a written `standalone` variant (grade the working tree or
  diff and reply in chat, no verdict file).
- **planner**: skip. Its value is MEOW's plan format and the run that follows.

Make it **opt-in** through `[claude].agents = ["explorer", ...]` in config
(default empty, or `["explorer"]`; see decisions). Every published agent costs
description tokens in every session.

**3. Back-compat.** Nothing is deprecated. The TOML schema and loader are
unchanged; `publish` is the only new key and it is optional. `.md` files keep
loading; the `meow-*` keys are optional too. Eject still emits TOML. Drift and
`ejected_from` keep hashing the shipped TOML. Diff, show, and validate gain one
thing: validate checks that each published agent renders standalone. Generated
files carry `meow-managed`, are ignored by discovery (no import loop when
`[custom].agents` points at `.claude/agents`), and are never ejected or diffed.
`meow agents show NAME --published` prints the generated `.md`.

**4. Security.** The generator writes only these keys: `name`, `description`,
`tools`, `disallowedTools`, `model`, `skills`, `color`, `maxTurns`, `effort`,
plus `meow-managed` and `meow-source`. It never writes `permissionMode`,
`mcpServers`, `hooks`, `memory`, `isolation`, `background`, `initialPrompt`, or
`omitClaudeMd`. A source that sets any of them is a validate warning. In a kit
it is an error.

Kit agents must declare `tools`; otherwise they inherit every tool, MCP tools
included. Approval follows the kit rule "prompts apply, commands wait":
- Changes to prose and description apply automatically, like skills.
- Changes that widen `tools`, drop a `disallowedTools` entry, or first publish a
  kit agent stay on hold until `meow kits update NAME --accept`. This reuses the
  `hooks_sha` mechanism as `agents_sha`.
- Project kits are pinned and reviewed in a commit, so no extra hold.

**5. Collisions and ownership.** Each scope publishes to a MEOW subfolder:
- project: `.claude/agents/meow/`, committed
- local: `.claude/agents/meow-local/`, listed in `.git/info/exclude`
- user: `~/.claude/agents/meow/`

Claude Code scans recursively, and with duplicate names it picks by filesystem
read order. So sync checks for a name clash across the whole tree, not by file
name. If the user owns an agent with that `name`, sync skips publishing it and
reports the clash; it never overwrites. Ownership comes from `meow-managed:
<scope>` plus `meow-source: <path>@<sha256>`. Sync rewrites or removes only
files that carry the key. A generated file that was hand-edited (hash mismatch)
is reported, then replaced, and the report names the source file to edit
instead. A `# generated by meow; edit <source>` comment goes in the frontmatter.

**6. Tests and docs.** See the phases. Docs stay under 200 lines: a new
`## Publishing to Claude Code` section in `docs/CUSTOM.md` (around 25 lines),
the `.md` key list in `docs/WORKFLOWS.md#agents`, and the `[claude]` key in
`INTEGRATIONS.md`. Replace the CUSTOM.md sentence "Agents and workflows need no
registration".

## Phases (each ships alone)

**P0: verify assumptions (no code).**
- MEOW's SDK runs leave `setting_sources` unset (SDK 0.2.172 default `None`), so
  published agents never appear inside MEOW runs. Add a test that pins this.
- Confirm that a new subfolder under an existing `.claude/agents/` is
  live-watched. Otherwise the first publish prints "restart Claude Code once".
- Confirm the plugin `agents/` folder loads from MEOW's install path.

**P1: `.md` parity for flat keys.**
- `load_agent` reads `meow-session`, `meow-lint-on-edit`, `meow-agents`, and
  `meow-ejected-from`.
- It rejects unknown `meow-*` keys and keeps ignoring Claude Code's keys.
- A `.md` that needs variants or fragments is impossible by construction, and
  the docs say to use TOML.
- Tests: `test_custom_agent_files.py` (table-driven parity: the same agent as
  TOML and `.md` gives an equal `CustomAgent`) and `test_frontmatter.py`.
- Risk: low. Today's `.md` files load unchanged.

**P2: standalone renderer + validation.**
- New `project/custom/standalone.py` (pure; render-only). Reuses `templates.py`
  and `rendering.py` with a standalone value set. Not added to `prompts.py`,
  which keeps run context.
- Adds the `publish` key (TOML) and the `meow-publish` key (`.md`). The default
  is false for TOML and true for `.md` in `.meow/agents/`.
- Adds the publish check to `meow agents validate`.
- Tests: every builtin either renders standalone or names its blocking variable
  (golden list); unresolved placeholders fail.
- Risk: low. Nothing is written to `.claude/` yet.

**P3: publish user, project, and local agents.**
- New `project/custom/agent_sync.py`, mirroring `flat_skills.py`: marker key,
  atomic write, collision report, `.git/info/exclude` for local.
- Wired into `meow sync` and `--auto`.
- `meow native verify` gains `agent_registration` and warns when published
  descriptions pass about 10k tokens.
- Discovery skips files that carry `meow-managed`.
- Tests: new `test_custom_agent_sync.py` (allowlist; never overwrites a user
  file; recursive name clash; removing a source removes its copy; local
  exclude), plus a test that `[custom].agents = .claude/agents` does not
  re-import.
- Risk: medium. It writes into the user's `.claude/`; the marker discipline
  limits the damage.

**P4: builtin plugin agents.**
- `meow:explorer` (and the reviewer `standalone` variant, if approved) is
  generated into the repo-root plugin `agents/`.
- A test fails when the generated file is stale, and offers a
  `python -m meow...` regeneration command.
- Gated by `[claude].agents`. If the plugin cannot be filtered per config, ship
  only explorer.
- Risk: description-budget creep; keep the list short.

**P5: kit agents** (after the kits branch merges; touches `kit_ops.py`).
- Kit agents publish through P3 with `tools` required.
- `agents_sha` holds widened tools.
- `meow kits update --accept` shows the tool diff.
- Tests go in `tests/kits/`.
- Risk: auto-delegation of third-party prompts. The required `tools`, the hold,
  and the allowlist bound it.

**P6 (optional): `meow agents eject NAME --format md`.** Only for agents with
no variants or fragments; refused otherwise.

Each phase runs `python -m pytest -q`, `ruff check .`, `ruff format --check .`.
AGENTS.md "Role prose" and "Definitions replace whole files" stay true. Add one
invariant line: "Published Claude Code agents are generated by
`agent_sync`; only allowlisted frontmatter is written".

## Decisions (approved)

1. E over D: TOML stays the source; generate `.md`, never copy; no rich YAML.
2. `[claude].agents` defaults to `["explorer"]`.
3. No standalone reviewer for now; revisit after P4.
4. `.md` in `.meow/agents/` publishes by default.
5. Allowlist: your list plus `effort`; never hooks, permissions, or MCP servers.
6. Kit agents must declare `tools`; widening and first publish wait for `--accept`.
7. Keep source names; skip and report on a clash, never overwrite.
8. Unknown `meow-*` keys are errors.

Start with P1.

# Custom sources

Definitions reload with configuration; no reinstall is required. Conventional
agent/workflow directories need no registration; see [scopes](WORKFLOWS.md#resolution-and-editing).
Use `[custom]` only for extra sources:

```toml
[custom]
skills = ["tools/meow/skills"]
agents = [{ path = "tools/meow/agents", roles = ["generator"] }]
workflows = ["tools/meow/workflows"]
```

Entries are path strings or tables with `path`. Skills/agents may also set
`roles`; workflows may not. Workflow selection uses `--workflow` or
`[workflow].default` (default `"default"`).

## Scope and paths

| Scope | Config | Allowed source paths |
|---|---|---|
| Local project | `.meow/config.local.toml` | Project-relative, absolute, or `~/...` |
| Project | `.meow/config.toml` | Repository-relative, inside the repo, not git-ignored |
| User | `~/.meow/config.toml` | Absolute, `~/...`, or relative to `~/.meow` |

Project sources resolve from the launch checkout even when execution uses a
worktree from another branch. Escaping/ignored project paths are rejected;
declare personal paths locally instead. Missing user directories log
`custom_source_missing` and appear in `native custom.skipped`; missing project
or local directories fail.

Whole-file precedence is local > project > user > builtin; duplicate names
within one scope fail. `meow native custom` reports overrides.

## Skills and agents

A skills source contains `<name>/SKILL.md`. Frontmatter requires `name`
matching its directory (lowercase letters/digits/hyphens) and `description`;
supporting files are retained. No `roles` means every role. Supported roles:
`explorer`, `planner`, `generator`, `reviewer`, `tester`, `review_fixer`,
`lint_fixer`, `docs_updater`, `issue_fetcher`, `gitlab_fetcher`, `github_fetcher`.
CLI serves these as a generated `meow-custom:<name>` plugin, alongside installed
and resolved built-in skills.

### Claude Code registration

Custom skills also load in Claude Code as namespaced plugin skills
(`/meow-local:NAME`, `/meow-project:NAME`, `/meow-user:NAME`). Each scope has
one plugin folder; put new skills in its `skills/`:

| Scope | Plugin folder | How Claude Code loads it |
|---|---|---|
| Project | `.claude/skills/meow-project/` (tracked) | In place from the project's `.claude/skills/` |
| Local | `.claude/skills/meow-local/` (git-ignored) | In place, as above |
| User | `~/.meow/plugin/` | `CLAUDE_CODE_PLUGIN_DIRS` in `~/.claude/settings.json` |

Project and local plugins need no marketplace or settings entry, so checkouts
and worktrees cannot collide; start Claude Code from the repository root.
Skills from other `[custom].skills` directories are copied into a generated,
git-ignored `meow-<scope>-linked` folder beside the plugin (`~/.meow/linked/plugin`
for the user), kept apart so rebuilding never touches hand-made skills. A rebuild keeps the
previous copies in `.skills-previous`; migrated old layouts are renamed `*.bak`.
Agents and workflows need no registration.

`meow sync` writes the manifests (and the user's plugin-dir entry). The MEOW
plugin runs the same sync as `meow sync --auto` when Claude Code starts and
right after Claude edits a `.meow/`, `SKILL.md`, or `.toml` file; it also
restores MEOW's `.gitignore` rules in an onboarded project and reports what it
cannot fix. Run `/reload-plugins` (or restart) to load changes; commit project
changes. `meow native verify` reports `skill_registration`,
`hook_registration`, `unshared_definitions` (project folders git ignores), and
`old_layout`.

Earlier versions used `.meow/plugin/`, `.meow/local/plugin/`, and `meow-*`
directory marketplaces, which clashed across checkouts ("Marketplace file not
found"). Any MEOW command, `meow sync`, or the startup sync migrates them: it
moves the plugins, rewrites `[custom].skills` entries naming the old folders,
and removes the old marketplaces from `.claude/settings*.json`. If Claude Code
still lists one, run `claude plugin marketplace remove NAME` once.

Agent TOML fields, model/session semantics, delegates, and Claude Code imports
are defined in [WORKFLOWS.md](WORKFLOWS.md#agents).
Configured agent sources retain planner/generator delegate compatibility,
or their configured roles; conventional definitions run only when named.

Tools remain subject to role permissions. Delegates are additionally capped to
the parent and exclude `Agent`; dropped tools log `custom_agent_tools_dropped`.
Prompts/skills grant no shell/network/MCP access. Discovered agent names are
valid permission roles; unknown names fail config validation.

Inspect with `meow agents show NAME`, `meow workflows validate`, or
`meow native custom --role ROLE`; `meow native verify` validates configuration.
Ejection/drift handling lives in [WORKFLOWS.md](WORKFLOWS.md#resolution-and-editing);
`/meow:customize` guides edits.

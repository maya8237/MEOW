# Custom sources

Definitions reload with configuration; no reinstall is required. Conventional
agent/workflow directories need no registration; see [scopes](WORKFLOWS.md#resolution-and-editing).
Install a git repository of definitions with [kits](KITS.md). Use `[custom]`
only for extra local sources:

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

Write skills in the scope's folder; `meow sync` publishes each one as
`<name>/` beside your own skills, where Claude Code watches for changes, so it
works as `/NAME` without a restart (`/reload-plugins` rescans if needed):

| Scope | Write skills in | Published to |
|---|---|---|
| Project | `.claude/skills/meow-project/skills/` | `.claude/skills/NAME/` (commit it) |
| Local | `.claude/skills/meow-local/skills/` | `.claude/skills/NAME/` (excluded via `.git/info/exclude`) |
| User | `~/.meow/plugin/skills/` | `~/.claude/skills/NAME/` |

Skills from other `[custom].skills` directories are published the same way.
Each published folder holds a `.meow-managed` marker: sync replaces or removes
only folders it marked, and reports (never overwrites) a skill of yours with
the same name. Hooks go into the settings file Claude Code reads for the scope:
`.claude/settings.json`, `.claude/settings.local.json`, `~/.claude/settings.json`;
other settings and hooks there are kept. Workflows need no registration.

Agents sync only when they opt in with `claude_agent = true` (or
`meow-claude-agent: true` in Markdown); see [agents](WORKFLOWS.md#agents). Each
becomes a Claude Code subagent from its standalone prompt, carrying only `name`,
`description`, `tools`, `model`, and `skills`, plus a `meow-managed` marker:

| Scope | Written to |
|---|---|
| Project | `.claude/agents/meow/NAME.md` (commit it) |
| Local | `.claude/agents/meow-local/NAME.md` (excluded via `.git/info/exclude`) |
| User | `~/.claude/agents/meow/NAME.md` |

Edit the source, not the copy: sync replaces it. Sync never overwrites a file
without the marker, and skips an agent whose `name` one of your own agents under
`.claude/agents/` already uses. Custom skill names lose the `meow-custom:` prefix.
Use it as `@agent-NAME`, by asking for it, or `claude --agent NAME`.

`meow sync` does all of this. The MEOW plugin runs it as `meow sync --auto`
when Claude Code starts and right after Claude edits a `.meow/`, `SKILL.md`, or
`.toml` file; it also restores MEOW's `.gitignore` rules in an onboarded project
and reports what it cannot fix. Plain `meow sync` and every `meow` command do
the same once `meow-local`/`-linked` folders exist, untrack any already
committed, and delivery never stages them or any local skill copy (`.meow-managed` = `local`). Commit project changes.
`meow native verify` reports `skill_registration`, `hook_registration`,
`agent_registration`,
`unshared_definitions` (project folders git ignores), and `old_layout`.
Updating MEOW migrates older installs on the next start: skills and hooks move
to these locations, and the user plugin-directory entry in
`~/.claude/settings.json` is removed.

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

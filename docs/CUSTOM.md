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

Your own agents (user, project, local) and kit agents sync by default when their
prompt renders without run values; one that does not is skipped quietly.
`meow-claude-agent: false` opts out and `true` forces it (an error if the prompt
needs run values). MEOW's builtin agents are never synced; an ejected copy of one
syncs only when it sets `true`; see [agents](WORKFLOWS.md#agents). Each becomes a
plain Claude Code subagent from its standalone prompt, carrying only `name`,
`description`, `tools`, `model`, and `skills`, in a folder MEOW owns:

| Scope | Written to |
|---|---|
| Project | `.claude/agents/meow/NAME.md` (commit it) |
| Local | `.claude/agents/meow-local/NAME.md` (excluded via `.git/info/exclude`) |
| User | `~/.claude/agents/meow/NAME.md` |

Edit the source, not the copy: sync replaces or removes any file in those
folders, never touches your agents elsewhere under `.claude/agents/`, and skips
an agent whose `name` one of those already uses. Custom skill names lose the `meow-custom:` prefix.
Use it as `@agent-NAME`, by asking for it, or `claude --agent NAME`.
Run `/reload-plugins` after the first sync creates `.claude/agents/`; no restart is needed.
MEOW's own runs never delegate to synced copies (each is denied by name).

`meow-claude-tools` gives the copy its own tools. The retired `[claude] agents`
table is ignored; the next sync deletes it and the builtin copies it made.

### Standalone prompt

A synced agent runs in Claude Code, where no run values (`plan_file`, `feedback`,
`output_file`, ...) exist. If the main prompt prints one, config loading fails
and names it. Fix it with a `standalone` variant, used only for the Claude Code
copy; MEOW's own runs keep the main prompt. Conditions on unset values are
false, `{{> fragment}}` is inlined, and `{{active_dir}}` reads "the current
working directory":

```markdown
---
name: change-review
description: Reviews changes.
meow-claude-agent: true
meow-claude-tools: Read, Grep, Glob
---
Review {{plan_file}} in {{active_dir}}. {{verdict_format}}

<!-- meow:variant standalone -->
Review the changes in {{active_dir}} and report concrete defects.
```

`meow agents eject NAME` writes the builtin as a Markdown agent, variants and
fragments included, with its `meow-ejected-from` drift check.

TOML agents from earlier releases still load, and the next `meow` command or
`meow sync` rewrites every one you own as `.md` (`meow agents migrate` does it
on demand). An eject nobody edited is brought up to the shipped version; an
edited one keeps its text and still reports drift.

`meow sync` does all of this, and refreshes Claude Code's cached MEOW plugin when it
lags the installed release (also checked at every start). The MEOW plugin runs it as `meow sync --auto`
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

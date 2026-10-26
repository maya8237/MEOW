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
(`/meow-local:NAME`, `/meow-project:NAME`, `/meow-user:NAME`). `<scope>/plugin/skills/`
(`.meow/local/`, `.meow/`, `~/.meow/`) is that scope's plugin. Skills from
`[custom].skills` directories are copied into a generated `<scope>/linked/plugin`
(`meow-<scope>-linked`). `meow skills sync` writes the manifests and enables
them in `.claude/settings.local.json`, `.claude/settings.json`, or
`~/.claude/settings.json` without touching other settings. Re-run it after
editing a configured skill; restart Claude Code to load changes.
`meow native verify` reports unregistered or stale skills as `skill_registration`.
The UI skill creator syncs automatically. Project plugins in git-ignored folders
are skipped.

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

---
name: customize
description: Create, override, attach, or remove MEOW skills, agents, and workflows in project, local, or user scope. Use for changing role prompts, tools, models, delegates, sessions, or workflow steps without editing MEOW source.
---

# Customize MEOW

Definitions load on every run; no reinstall is needed. Use the Workflows guide in
`meow ui` or the [online docs](https://maya8237.github.io/MEOW/) for agent/workflow
formats and the Integrations guide there for configured sources,
skills, models, and permissions. Dependency or install problems: [dependencies](../_shared/dependencies.md).

## See what is customized, by scope

Show the user where each scope's customizations and configuration live (the
table below). On Windows, write user paths as `$env:USERPROFILE\.meow\...`,
not `~`. `meow agents list` and `meow workflows list` report what is in effect
and its scope. Built-ins are viewed with `list`/`show` and changed by ejecting a copy into a scope (section 2). Last, mention that `meow ui` (`--no-browser` prints the URL)
shows the same catalog visually and manages workflows and sessions.

## 1. Resolve the request and scope

Use a skill for reusable instructions, an agent definition for a role with its
own prompt/tools/model, and a workflow for a different sequence of operations.
Ask only for choices the request leaves open; use clear defaults when possible.

| Scope | `agents/`, `workflows/`, `hooks/` in | Skills in | Config |
|---|---|---|---|
| Project | `.meow/` | `.claude/skills/meow-project/skills/` | `.meow/config.toml` |
| Local project | `.meow/local/` | `.claude/skills/meow-local/skills/` | `.meow/config.local.toml` |
| User | `~/.meow/` | `~/.meow/plugin/skills/` | `~/.meow/config.toml` |

These conventional directories need no `[custom]` entry; skills and hooks also
need the Claude Code sync below. Higher scopes replace
whole definitions: local > project > user > builtin. Duplicate names within
one scope fail. Share project definitions in version control; never put a
personal absolute path in shared config. Extra directories can be registered
through `[custom]` using its documented scope/path rules.

## 2. Inspect before editing

Run `meow agents list` and/or `meow workflows list` with `--work-dir PATH` when
needed. Use `show NAME` to inspect the effective definition and its scope.
To customize a shipped definition, eject it:

```bash
meow agents eject reviewer --scope project
meow workflows eject quick --scope local
```

Eject copies the shipped file even when an override exists elsewhere and
refuses to overwrite. It records its shipped origin hash. Explain any override
the new file will create. Update drift is reported by `show` and workflow
`validate`; use `meow agents diff NAME` to inspect agent differences.

## 3. Write the definition

**Agent:** `<name>.toml`, with lowercase letters/digits separated by single
hyphens (at most 64 characters). `description` and `prompt` are required.

```toml
description = "Check migrations for locking and rollback safety"
model = "inherit"
tools = ["Read", "Grep", "Glob", "Write"]
skills = []
agents = []
session = "one-shot"
lint_on_edit = false
prompt = '''
Review migrations in {{active_dir}}. {{feedback}}
Write the verdict to {{output_file}}.
{{verdict_format}}
'''
```

Use `session = "persistent"` to retain a role session within a workflow loop.
Optional `[variants]` and `[fragments]` keep alternate prompts and reusable
text. Templates support variables, fragments, and conditional blocks; unknown
values fail before an agent starts. A missing dotted key is empty. Explicit
`[models].role` wins over the agent model; hyphens map to underscores in role
config. `explorer` is customizable. Markdown agents remain a Claude Code import
format, but TOML is preferred for full role definitions.

An agent's `agents` list names explicit delegates. Each delegate's tools are
capped to its parent and exclude `Agent`. Agents registered through
`[custom].agents` also retain planner/generator delegate compatibility, with
optional `roles`. Conventional definitions are used when explicitly named.
Resolved tools remain subject to `[permissions]`; prompts never bypass policy.

**Hook:** `<name>.toml` under `hooks/` with `description`, `event`, `command`
(optional `matcher`, `timeout`, `roles`). Exit 2 denies or feeds back; hooks never
grant permissions. Events and `meow native hook` are covered in the Hooks guide in
`meow ui` or the [online docs](https://maya8237.github.io/MEOW/).

**Workflow:** `<name>.toml`, with `description` and a `steps` list. Choose from
`uses = "builtin.name"`, `agent`, `gate`, `loop`, `parallel`, and `call`.

```toml
description = "Review migrations and run configured checks"
[[steps]]
id = "migrations"
gate = "migration-checker"
[[steps]]
uses = "builtin.checks"
```

Blocking loops require a gate or `builtin.review`. Parallel branches must use
read-only agent/gate definitions as specified in the Workflows guide in `meow ui` or the
[online docs](https://maya8237.github.io/MEOW/). Gates
need a verdict artifact with `SUMMARY:` and `STATUS: PASS|FAIL`; MEOW enforces
the contract even if an override omits it. Conditions can use run flags,
`round`, `steps`, and string `inputs`. Run with `--workflow NAME` and repeated
`--input name=value`, or set `[workflow].default = "NAME"`. Custom workflow
execution uses CLI mode in `/meow:run`.

**Skill:** `<name>/SKILL.md` in the scope's skills folder (table above), with
frontmatter `name` matching its directory and `description` explaining when it
applies. Supporting files may sit beside it. Use
`[custom].skills` only for an existing directory elsewhere or to limit `roles`.
Definition `skills` and `[agent_skills]` can also name installed skills.

Register extra directories with `[custom]` using the configuration example in the
Integrations guide in `meow ui` or the [online docs](https://maya8237.github.io/MEOW/).
Workflow sources do not take `roles`.

## 4. Verify and report

Run `meow workflows validate [NAME]`, `meow agents show NAME`, and
`meow native verify` as applicable. Use `meow native workflow NAME` for expanded
JSON, `meow native prompt ROLE` for resolved role instructions, and
`meow native custom --role ROLE` for compatibility delegates and custom skills.
For project scope, confirm files are committable with `git status`.

Report paths, scope, overrides, selection/config changes, and verification.
Skills and hooks register with Claude Code automatically: the MEOW plugin
syncs after Claude edits them and at startup. Outside Claude Code, run
`meow sync` (`--check` reports only). Tell the user to restart Claude Code and
commit project changes. Skills appear as `/meow-<scope>:<name>`.
Role-limited and `sprint_*` hooks stay in MEOW's SDK agents and need no sync.
Commit only when the user requests it. Remove a file to remove a definition;
remove a configured directory entry to unregister its contents. Follow the
chosen scope's path rules when moving definitions.

## Changing MEOW source

Editable shipped role prose lives in
`src/meow/project/custom/builtin/agents/*.toml`, shipped workflows beside them
under `builtin/workflows/`. `project/prompts.py` owns computed context and gate
contracts. Engine and permission changes belong in their owning packages.
Update the CLI/native documentation together when changing shared behavior.

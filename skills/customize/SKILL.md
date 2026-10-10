---
name: customize
description: Create, attach, or remove custom MEOW skills and agents, choosing whether they live in the repository (project scope), in an ignored local config (local project scope), or anywhere on the user's computer (user scope). Use when a user wants MEOW roles to follow their own skill, delegate to their own agent, or change which roles receive one.
---

# Customize MEOW

MEOW discovers custom skills and agents from the directories listed in a
`[custom]` table on every run, so nothing needs reinstalling. The full schema
is in `docs/INTEGRATIONS.md` ("Custom skills and agents"); this skill walks a
user through making and registering one.

## 1. Pin down the request

Ask only what the request leaves open:

1. **What:** a skill (instructions a role follows when relevant), an agent
   (a delegate the planner or generator can hand work to), or both.
   Prefer a skill unless the work needs its own context window or a narrower
   tool set; then it is an agent.
2. **Which roles:** skills may target `explorer`, `planner`, `generator`,
   `reviewer`, `tester`, `review_fixer`, `lint_fixer`, `docs_updater`, or the
   fetchers; omit `roles` for every role. Agents attach to `planner` and/or
   `generator` (the roles that can delegate).
3. **Scope and location:**

   | Scope | Config file | Directory rules | Use when |
   |---|---|---|---|
   | Project | `.meow/config.toml` | relative path inside the repo, not git-ignored | the team should share it in version control |
   | Local project | `.meow/config.local.toml` | relative, absolute, or `~/...` | only this user, only this project |
   | User | `~/.meow/config.toml` | absolute, `~/...`, or relative to `~/.meow` | this user, every project |

   Let the user choose the directory. Suggest `tools/meow/skills` and
   `tools/meow/agents` for project scope and `~/.meow/skills` /
   `~/.meow/agents` for user scope, but use whatever they prefer. For project
   scope, confirm the directory is not ignored
   (`git check-ignore -v <dir>` prints nothing).

If a reasonable default is obvious, state it and continue.

## 2. Check what already exists

Run `meow native custom` (add `--working-dir` if needed). It lists every
custom skill and agent in effect with its scope and directory, plus
`overrides`. A new definition with an existing name in a **higher** scope
replaces it; in the **same** scope it is a configuration error. Mention any
override the user would create.

## 3. Write the definition

**Skill:** `<dir>/<name>/SKILL.md`, where `<name>` is lowercase letters,
digits, and single hyphens and matches the directory.

```markdown
---
name: house-style
description: Apply this team's API naming and error-handling conventions when writing or reviewing endpoint code.
---

# House style
...
```

The description decides when a role reaches for the skill, so say when it
applies. Supporting files may sit beside `SKILL.md`. When the user wants help
writing a substantial skill, offer `skill-creator`.

**Agent:** `<dir>/<name>.md`; the body is the prompt.

```markdown
---
name: migration-checker
description: Check database migrations for locking, ordering, and rollback safety. Use after editing files under migrations/.
tools: Read, Grep, Glob, Bash
model: inherit
skills: house-style
---

You review database migrations...
```

`tools` is capped to the parent role's tools (the planner has Read, Grep, Glob,
Write; the generator adds Edit and Bash) and never includes Agent; omit it to
inherit them. `explorer` is a reserved name. Claude Code-only keys such as
`color` are ignored, so the file also works as a Claude Code subagent.

## 4. Register the directory

Add or extend `[custom]` in the chosen config file. Do not touch other tables.

```toml
[custom]
skills = ["tools/meow/skills"]
agents = [{ path = "tools/meow/agents", roles = ["generator"] }]
```

A directory only needs registering once; later files in it are discovered
automatically. Never put a personal path in `.meow/config.toml`.

## 5. Verify and report

1. `meow native custom --role <role>` for each targeted role: the new entry is
   listed with the expected scope.
2. `meow native verify` succeeds (it loads and validates the whole config).
3. For project scope, `git status` shows the new files as committable.

Report the definition's path, the config file changed, the roles that receive
it, any override it creates, and what verification ran. Commit only if the
user asks.

## Removing or moving

Delete the file to remove one definition, or remove the directory's entry from
`[custom]` to drop all of them. Moving a project definition to user scope means
moving the file and registering its new directory in `~/.meow/config.toml`.

## Changing MEOW itself

Built-in roles, prompts, and orchestration are MEOW source code, not
customizations. Only change them when the user is working on the MEOW
repository: prompts live in `src/meow/project/prompts.py`, roles in
`src/meow/agents/`, and native parity in `skills/_shared/native-mode.md`.
Everything else belongs in `[custom]`, `[agent_skills]` (installed skills by
identifier), or `[permissions]`.

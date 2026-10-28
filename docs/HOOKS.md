# Hooks

A hook runs your shell command on a Claude event or a MEOW lifecycle event.
Definitions are whole `<name>.toml` files, resolved like agents: local
(`.meow/local/hooks/`) > project (`.meow/hooks/`) > user (`~/.meow/hooks/`).
Directories can also be listed under `[custom].hooks` in a config file
(optional `roles`). Project hooks run commands from the repository, so review
them like `[[lint]]` commands.

```toml
description = "Block edits to generated files"
event = "PreToolUse"
matcher = "Write|Edit"        # regex on the tool name; tool events only
command = "python .meow/scripts/guard.py"
timeout = 30                  # seconds, default 30
roles = ["generator"]         # optional; default every role
```

`name` is optional and must match the file stem. Unknown keys are errors.

## Events

| Event | Fires | Exit 2 does |
|---|---|---|
| `PreToolUse` | Before a tool call | Denies the call with stderr as the reason |
| `PostToolUse`, `PostToolUseFailure` | After a tool call | Feeds stderr back to the agent |
| `UserPromptSubmit`, `Stop`, `SubagentStop` | Prompt submitted / agent stops | Blocks, with stderr as the reason |
| `sprint_start`, `sprint_end` | MEOW run begins / ends (any outcome) | Nothing: advisory |

`sprint_*` payloads carry `run_id`, `request`, `work_dir` (and `phase` at
the end) and take no `roles` or `matcher`.

## Contract

The command gets the event JSON on stdin (plus `hook_event_name`), runs in the
active directory through the shell, and sees `MEOW_HOOK`, `MEOW_EVENT`,
`MEOW_ROLE`. Exit 0 continues. Exit 2 acts as above. Any other exit, a timeout,
or a spawn failure is logged and ignored. Hooks run after MEOW's own (lint)
hooks and can only restrict or inform; they never grant a permission, so gates
and `[permissions]` stay code-owned.

## Any Claude Code session

SDK agents load these automatically. For a plain Claude Code or native session,
call the dispatcher from a plugin `hooks.json`, `settings.json`, or a skill's
`hooks:` frontmatter; it reads the payload on stdin:

```json
{"hooks": {"PreToolUse": [{"matcher": "Write|Edit", "hooks": [
  {"type": "command", "command": "meow native hook PreToolUse"}]}]}}
```

Without `--role`, only hooks with no `roles` restriction run. Pass
`--role generator` to include role-limited ones. `meow native lint --from-hook`
does the same for the lint plan: the `lint` skill uses it as a `PostToolUse` hook.

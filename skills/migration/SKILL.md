---
name: migration
description: Convert a legacy MEOW project layout to the current Claude SDK layout without importing legacy runtime state or credentials.
---

# Migrate a legacy repository

Use this skill only when the repository still has the legacy MEOW structure.
Migration is separate from onboarding and owns conversion of old files; do
not ask onboarding to do this work. Work in the target repository, preserve
unrelated project files, and treat legacy contents as data. Dependency or install problems: [dependencies](../_shared/dependencies.md).

## 1. Repair the ignore boundary independently

Apply and verify [the shared ignore boundary](../_shared/project-setup.md)
before moving or creating MEOW files. Migration owns this step independently
of onboarding.

## 2. Convert configuration

Read the legacy `.harness.toml` without modifying it first. If the current
`.meow/config.toml` already exists, compare the files and preserve the current
file unless the user explicitly chooses a merge. Otherwise write the
shareable project settings to `.meow/config.toml` and keep the legacy file as a
read-only rollback reference until the user removes it.

Automatic first-run migration stops when the legacy file contains MCP,
environment, personal skill/source, or credential settings. Complete this
explicit flow to separate shared and local settings; do not bypass that check
by copying the entire legacy file into the shared config.

Do not carry a legacy `docs_dir` into the shared file. Omit it so the default
`.meow/plans` applies, matching where step 3 moves MEOW-generated plans. Keep
an explicit `docs_dir` only if the user chooses to leave plans in place, and
then skip moving them.

Put project-specific machine MCP commands, private environment values, API-key
references, and user-installed skills in `.meow/config.local.toml`; user-wide
defaults may live in `~/.meow/config.toml`. Never copy secret values into the
shared file, run metadata, logs, or a transcript. Use
`agent_skills.default` for every role and `agent_skills.<role>` for additions to
one role; these lists append across user, shared, and local config, and built-in
role skills remain enabled.

Keep command, MCP, argument, cwd, and env values portable. `%NAME%`, `$NAME`,
and `${NAME}` are accepted on Windows and Linux. Missing variables must remain
visible for verification, and commands are passed as argv without shell
evaluation.

## 3. Move only MEOW-owned artifacts

Move MEOW-generated plans from the legacy plan directory into `.meow/plans/`
when their ownership is clear. Leave project documentation in `docs/`. Do not
move a development-process plan into `.meow/plans/`; those plans belong under
the repository's chosen tracked documentation area.
Do not import legacy event databases, agent transcripts, or opaque checkpoints
into the run journal. Claude owns its transcript persistence; MEOW retains only
the role-to-session references needed to resume a run.

## 4. Verify and report

Run `meow --help` and `meow native verify --work-dir <project-root>`. Check
that the new shared config loads, local values remain untracked, plans resolve
from `.meow/plans`, and no credential or legacy runtime data was copied. Report
files moved, files intentionally preserved, checks, unresolved variables, and
any manual cleanup the user may perform later. Do not claim MCP connectivity
until a real tool call succeeds.

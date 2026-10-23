# Local UI and sessions

`meow ui [--port 8765] [--no-browser]` serves the bundled React app on
127.0.0.1. Node.js is unnecessary for use; Ctrl+C stops the server.
Frontend development/build instructions are in [frontend/README.md](../frontend/README.md).

## Catalog

Browse workflows, agents, and skills across scopes and repositories remembered
in `~/.meow/repos.toml`; select a repository to narrow results.
Search name/description/scope with Ctrl+K (Cmd+K on macOS). Visible pages refresh
every two seconds. Workflow details show steps, expandable source, and CLI/skill commands.

`+ New` copies a definition under a new name or creates a blank skill.
Save at project (`.meow/`), local (`.meow/local/`), or user (`~/.meow/`) scope.
Skills go under that scope's `plugin/skills/`; the first skill also creates
its plugin manifest and registers it in the matching Claude settings file:
project `.claude/settings.json`, local `.claude/settings.local.json`, or
user `~/.claude/settings.json`. Use `/meow:customize` to write the contents.
See [WORKFLOWS.md](WORKFLOWS.md) for resolution and override rules.

## Launching sessions

Open **Sessions → New session**, select a registered repository and command,
or start from a workflow, skill, or supported Claude agent.
The launcher previews and validates the exact command. CLI commands use argument
arrays and have no interactive stdin. Feature runs execute automatically in a
worktree by default and commit/push after verification. In-place options select
no delivery (`-n`), commit only (`-nc`), or commit/push (`-np`).

Advanced arguments derive flags, aliases, types, choices, and descriptions from
the CLI parser. Check a flag and supply values; Remove clears the flag and values.
Managed/incompatible flags remain visible with an explanation.
Review/run requests can be empty when source flags supply the work.

For orchestration, enter the objective in Request and options such as `--from`,
`--max-parallel`, `--dry-run`, `--test`, `--no-pr` as argument tokens.
These recipes start new orchestrators; use terminal status/resume/cancel for
lifecycle controls. Orchestration may push after review even with `--no-pr`.

## Claude permissions

Claude sessions require a resolvable installed/authenticated CLI and MEOW
plugin/agent. Unsupported capabilities are reported before launch.
Default flags are `--permission-mode dontAsk` and, when supported,
`--permission-prompts none`; unanswered permission requests are denied.
The labeled bypass option is off by default; enabling it passes
`--allow-dangerously-skip-permissions --permission-mode bypassPermissions`.

## Attempts and recovery

Recipes, attempts, and separate logs live in `.meow/ui/sessions/`.

- Edit changes the next attempt's recipe, never a running attempt or saved output.
- Rerun starts fresh, including a fresh feature worktree.
- Duplicate creates an editable draft without starting it.
- Resume continues a stopped resumable run via `meow resume ID --continue`
  or `claude --resume SESSION_ID`. Product pauses can receive clarified requests;
  repair remains an explicit choice. Completed runs offer rerun instead.
- Stop preserves output and resume identity.
- Archive reversibly hides inactive sessions without deleting files or worktrees.

Imported MEOW runs retain recorded facts and supported actions. Historical output
appears only when a background log exists. Rerun/duplicate opens an editable
recipe but cannot recover flags absent from the journal.

## Scheduling

In the launcher choose **Schedule for later → Run at → Preview command →
Schedule session**. Dates use browser-local time and are stored as UTC.
The reviewed recipe is frozen for that execution, regardless of later edits.
Session details show the time, **Edit time**, and cancellation controls.
**Save time** changes only the pending trigger's time, preserving recipe and ID;
started/completed/cancelled schedules cannot be edited.
See [CLI scheduling](CLI.md#scheduling-a-run) for platform and lifecycle rules.

## Docs

The **Docs** page shows this project's own Markdown guides and skills,
read from the installed package, with no hand-copied text.
Search covers every section; choose a hit to open it at that heading.
Ctrl K focuses the search. Links between docs open in-app; external links
open in a new tab. Raw HTML in Markdown is not rendered.

The same docs are published at the hosted site linked from **Online docs**
(GitHub Pages, deployed from `main`). The local app only links to it and
never fetches it, so Docs works offline.

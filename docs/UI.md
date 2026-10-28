# Local UI

`meow ui [--port 8765] [--no-browser]` serves the bundled React app on
127.0.0.1. Node.js is unnecessary for use; Ctrl+C stops the server.
Frontend development/build instructions are in [frontend/README.md](../frontend/README.md).

## Catalog

Browse workflows, agents, skills, and hooks across scopes and repositories remembered
in `~/.meow/repos.toml`; select a repository to narrow results.
Search name/description/scope with Ctrl+K (Cmd+K on macOS). Visible pages refresh
every two seconds. Workflow details show steps, expandable source, and CLI/skill commands.

The catalog is read-only. To add or change a definition, run `/meow:customize`
in Claude Code; see [WORKFLOWS.md](WORKFLOWS.md) for resolution and override rules.

## Sessions

**Sessions** lists saved sessions and MEOW runs across repositories, searchable
by name, command, and state. Archived sessions are hidden until requested.
Select a record to see its attempts, linked run facts, saved settings, and
recorded output (wrap, copy, or download). Active output follows live.
Sessions and runs are started, resumed, and stopped from the terminal
(`meow run`, `meow resume`) or Claude Code; the UI never launches or changes them.
Historical output appears only when a background log exists.

## Docs

The **Docs** page shows this project's own Markdown guides and skills,
read from the installed package, with no hand-copied text.
Search covers every section; choose a hit to open it at that heading.
Ctrl K focuses the search. Links between docs open in-app; external links
open in a new tab. Raw HTML blocks and tags are omitted from the reader.

The hosted site linked from **Online docs** starts with short setup, skills,
and customization pages. **Reference** opens the complete searchable guides;
skill instructions and format details also link to the original Markdown on
GitHub. The source guides are preserved in full. GitHub Pages deploys from
`main`. The local app only links to it and never fetches it, so Docs works offline.

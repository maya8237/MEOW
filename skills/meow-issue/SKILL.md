---
name: meow-issue
description: Fetch a Jira issue (or the latest one in the configured project) and solve it end to end in a pushed worktree branch. Use when the user wants meow to pick up a Jira ticket and turn it into a branch, unattended or otherwise.
---

# meow-issue

Runs `meow issue` against the current project: fetch a Jira issue through a
configured Jira MCP server, solve it through the same
plan -> implement -> review loop `/meow:sprint` uses, in a dedicated
worktree, then push the resulting branch.

1. The current project must have a `.harness.toml` at its root with `[jira]`
   (at least `project_key`) and `[jira.mcp]` (`command`, and `args` if the
   server needs them) set — see this repo's GUIDE.md §2 and §7. If either is
   missing, tell the user and point them at GUIDE.md rather than guessing at
   Jira MCP settings.
2. If the user gave a specific issue key (e.g. `PROJ-123`), pass it through.
   Otherwise omit it — `meow issue` picks the most recently created issue in
   `[jira].project_key` on its own.
3. Run, from the project root:

   ```bash
   meow issue [ISSUE-KEY] --working-dir "<project-path>"
   ```

   If `meow` isn't found on PATH, tell the user to install it first (this
   repo's README: a venv with `pip install -e .`, or `pipx install -e .` for
   a global command) — don't guess at a path to some venv.
4. This can take several agent rounds and prints progress as it goes
   (`[planner]`, `[generator]`, `[reviewer]`, plus Jira preflight/fetch
   lines) — stream that output to the user rather than waiting silently.
5. On success it prints one JSON line with the issue key and the pushed
   branch name — report both back to the user. On failure (no active Jira
   MCP, no matching issue, no `origin` remote, or the sprint not passing
   within `max_rounds`), report the error message verbatim; it already names
   the missing config or points at the review file.

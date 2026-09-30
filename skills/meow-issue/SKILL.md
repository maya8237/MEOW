---
name: meow-issue
description: Fetch a Jira issue (or the latest one in the configured project) and solve it end to end in a pushed worktree branch. Runs natively in this Claude Code session by default. Use when the user wants meow to pick up a Jira ticket and turn it into a branch.
---

# meow-issue

Fetch a Jira issue, solve it through the same plan -> implement -> review loop
`/meow:sprint` uses, in a dedicated branch worktree, then push the branch.
Runs **natively** by default, using the Jira tools already connected to this
session. Read [`../_shared/native-mode.md`](../_shared/native-mode.md) (relative
to this skill's base directory) first. Use **CLI mode** (bottom) only if the
user explicitly asks for the unattended, separate-process run.

## Native mode

1. The project's `.harness.toml` must have `[jira]` with `project_key`
   (`branch_prefix` optional, default `issue/`). Read that file for those two
   values. The `[jira.mcp]` table is **not** used here: fetch through the Jira
   MCP tools connected to this session (look for tools whose names contain
   `jira`). If none are connected, or `[jira]` is missing, say so and stop; point
   at docs/INTEGRATIONS.md in the meow repo.
2. If the user gave an issue key, fetch it; otherwise fetch the most recently
   created issue in `project_key`. Need `key`, `summary`, `description`; if any is
   missing, stop and report.
3. Feature name: `issue-<key>` lowercased with anything outside `A-Za-z0-9._-`
   turned into `-`. Branch: `<branch_prefix><KEY>`. Run
   `meow native prepare --name "<feature>" --branch "<branch>" --working-dir "<project-path>"`.
   Report failures verbatim and stop. Work only in the returned `active_dir`.
4. Round counter: `meow native round <plan_file> --reset --active-dir <active_dir>`.
   Plan as in `/meow:sprint`, with the request text
   `Resolve Jira issue <KEY>: <summary>` + blank line + `<description>`.
   Do not pass `--worktree` when dispatching reviewers in this flow (CLI parity).
   Only ask for plan approval if the user asked for it.
5. Run the shared review loop until PASS. If a round comes back `exhausted`,
   report that the issue was not resolved within `max_rounds` and give the
   review file; do not push.
6. On PASS run `meow native push "<branch>" --active-dir <active_dir>` (needs an
   `origin` remote; report its error verbatim otherwise). Then report one line:
   `{"issue": "<KEY>", "branch": "<branch>"}`.

## CLI mode

```bash
meow issue [ISSUE-KEY] --working-dir "<project-path>"
```

Omit the key to use the latest issue in `[jira].project_key`. It needs
`[jira]` and `[jira.mcp]` in `.harness.toml` and can run fully unattended. If
`meow` isn't on PATH, tell the user to install it (README: `pip install -e .` in
a venv, or `pipx install -e .`). Stream its progress; on success it prints one
JSON line with the issue key and pushed branch, on failure report its message verbatim.

# docs/INTEGRATIONS.md — Jira, GitLab, and scheduled runs

Reference material for the optional, advanced parts of onboarding a project
onto meow. Everyday setup lives in [GUIDE.md](../GUIDE.md); come here only if
you need `meow issue`, `meow gitlab-review`, an unattended scheduled run, or a
full error-message reference.

---

## `[jira]` / `[jira.mcp]` — for `meow issue`

| Field | Required | Default | Notes |
|---|---|---|---|
| `[jira].project_key` | Only for `issue` | — | Jira project searched for "the latest issue" when no issue key is given. |
| `[jira].branch_prefix` | No | `"issue/"` | Prefix for the branch `meow issue` creates and pushes. |
| `[jira.mcp].command` | Only for `issue` | — | Program that launches the Jira MCP server, e.g. `"uvx"`. |
| `[jira.mcp].args` | No | `[]` | Its arguments, e.g. `["mcp-atlassian"]`. |

Its server reads its own credentials from the environment (`JIRA_URL` plus
either `JIRA_USERNAME`+`JIRA_API_TOKEN` for Cloud or `JIRA_PERSONAL_TOKEN` for
Server/Data Center) — never put them in `.harness.toml`.

## `[gitlab.mcp]` — for `meow gitlab-review`

Unlike `[jira]`, there's no `project_key`-style field — the merge request URL
is passed on the command line each time.

| Field | Required | Default | Notes |
|---|---|---|---|
| `[gitlab.mcp].command` | Only for `gitlab-review` | — | Program that launches a GitLab MCP server exposing merge-request read tools. |
| `[gitlab.mcp].args` | No | `[]` | Its arguments. |
| `[gitlab.mcp].env` | No | `{}` | Environment variables passed to the launched server, e.g. `GITLAB_URL`, `GITLAB_TOKEN`. |

**Security note:** unlike `[jira.mcp]`, `[gitlab.mcp].env` is read straight
from `.harness.toml` and passed to the launched server as-is. `.harness.toml`
is an ordinary, committed project file — putting a real GitLab token in
`[gitlab.mcp].env` commits that secret to your repo's history in plain text,
hard to fully revoke even after rotating it. If that's not acceptable, keep
the token in your actual shell/CI environment and reference it however your
chosen GitLab MCP server supports variable expansion, or gitignore
`.harness.toml` (or a local override of it) if your project's conventions
allow that.

---

## Running `meow issue` on a schedule (Windows Task Scheduler)

`meow issue [ISSUE-KEY]` fetches a Jira issue (or the most recently created
one in `[jira].project_key` if you omit the key), solves it through the same
plan/implement/review loop as `meow run`, inside its own worktree on branch
`<branch_prefix><ISSUE-KEY>` (default `issue/<ISSUE-KEY>`), then pushes that
branch to `origin`. On success it prints one JSON line to stdout —
`{"issue": "PROJ-123", "branch": "issue/PROJ-123"}` — and exits 0; any failure
(no active Jira MCP, no matching issue, the sprint not passing within
`max_rounds`, or the push failing) raises before that line is printed, and the
process exits non-zero.

**Never pass `--manually-approve-plan`/`-m` on a scheduled run** — it prompts
on stdin for approval before the generator starts, and a scheduled task has no
console attached to answer it, so the run just hangs instead of completing or
failing cleanly.

**Prerequisites**: `[jira]`/`[jira.mcp]` set in `.harness.toml` (above), a
Jira MCP server reachable with those settings (this repo assumes
[`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian), installable
with `uvx` so no separate install step is needed), its credentials in the
environment, and an `origin` remote the scheduled task's account can push to
(e.g. an SSH key or stored credential, not an interactive prompt).

**Persistent logs**: a scheduled task has no attached console, so set
`MEOW_LOG_FILE` to a path before running — every run appends its key=value log
lines there instead of only writing to stderr (`MEOW_LOG_LEVEL` also works the
same way `meow run` uses it, e.g. `DEBUG` for more detail).

**Registering the task** — from an elevated PowerShell prompt, using
`schtasks` (adjust the venv path, working directory, issue key or omit it for
"latest", and schedule):

```powershell
schtasks /Create /TN "meow-issue" /SC DAILY /ST 09:00 /RL LIMITED /TR (
    '"C:\path\to\meow\.venv\Scripts\meow.exe" issue' +
    ' --working-dir "C:\path\to\target-project"'
)
```

`schtasks /TR` runs with a minimal environment, so set `MEOW_LOG_FILE` and the
Jira credentials as that account's persistent user/system environment
variables (`setx`) rather than relying on variables set in your interactive
shell. Verify the task once with `schtasks /Run /TN "meow-issue"`, then
`Get-Content <MEOW_LOG_FILE> -Tail 50` to confirm it ran and to read its
result.

---

## If something's missing

| Missing / wrong | Result |
|---|---|
| `.harness.toml` | `FileNotFoundError` before any agent runs |
| No `[[lint]]` entries or `lint_command` | `ValueError`: no lint command defined |
| Unknown key in a `[[lint]]` table (often a top-level key placed after it) | `ValueError` naming the entry and key |
| No architecture doc anywhere under `docs/` | No error — reviewer's SOLID/SRP pass finds nothing to Glob/Read, so it has no project-specific boundaries to check, just its generic mixed-responsibility rule |
| Other `docs/` files (`tech-debt-tracker.md`, `core-beliefs.md`, etc.) | No error — no role goes looking for them specifically, only opportunistically via each role's docs scan |
| `AGENTS.md` | No effect on meow — human-facing only |
| `meow issue` run without `[jira]`/`[jira.mcp]` | `ValueError` naming the missing table/key, before any agent runs |
| `meow issue` run with no Jira MCP actually reachable | `RuntimeError` from the preflight check — it requires an actual `mcp__jira__*` tool call to succeed, not just a text claim of success |
| `meow issue` run with no `origin` remote | `RuntimeError` after the sprint passes, before attempting to push |
| `meow gitlab-review` run without `[gitlab]`/`[gitlab.mcp]` | `ValueError` naming the missing table/key, before any agent runs |
| `meow gitlab-review` run with no GitLab MCP actually reachable | `RuntimeError` from the preflight check — it requires an actual `mcp__gitlab__*` tool call to succeed, not just a text claim of success |
| `meow lint-fix` (standalone, not `--report-only`) never gets lint clean within `max_rounds` | `LintFixError` including the still-failing commands' raw output |
| `meow review-fix-review` given a GitLab MR review file | `RuntimeError` explaining there is no local checkout of the merge request's code to fix |
| `meow review-fix-review` with `--review-file` omitted and no review file anywhere in `docs_dir` | `FileNotFoundError` naming `docs_dir` and pointing at `--review-file` |
| `meow review-fix-review` (either flavor) never passes within `max_rounds` | `RuntimeError` naming the review file, same stop/raise shape as `meow review` |

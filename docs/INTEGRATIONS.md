# docs/INTEGRATIONS.md — Jira, GitLab, GitHub, and scheduled runs

Reference material for optional project setup. MEOW sets up a project's base
configuration itself on first use; run `/meow:onboard` for the add-ons below;
this guide covers monorepo lint/test configuration, tester mode, integrations,
scheduled Jira runs, and error messages.

---

## Monorepo lint and tester

`.meow/config.toml` is optional. If it is absent, MEOW starts with its built-in
defaults and no lint commands. Configuration priority is the ignored local
`.meow/config.local.toml`, project `.meow/config.toml`, user
`~/.meow/config.toml`, then built-in defaults. String values may reference environment
variables with either POSIX syntax (`$HOME` or `${HOME}`) or Windows syntax
(`%USERPROFILE%`); expansion is applied recursively to configured tables and
lists. Unknown variables are left unchanged.

Each `[[lint]]` entry may set `cwd`, `include`, `exclude`, `args`, `env`, and
`timeout`. `include` and `exclude` are repository-relative path prefixes. A
per-file lint command runs only for edits inside its `cwd` and included paths;
project-wide lint runs every configured entry. `gate` defaults to `true`.
`lint_timeout` (60 seconds by default) supplies the timeout unless the entry
sets its own `timeout`.

`meow run --test` enables configured tests and exploratory testing after a
passing plan review. `meow review --plan PATH --test` performs one
report-only pass; add `--fix` to use the shared round budget. Tester mode is
not available for other review sources or `--review-file`. Without configured
tests, the tester can inspect and run documented project tests.

```toml
[models]
tester = "haiku"

[tester]
test_dirs = ["apps/web/tests", "services/api/tests"]
# `architecture_files` is optional; default lookup is docs/ARCHITECTURE.md,
# then ARCHITECTURE.md.
architecture_files = ["docs/architecture/backend.md"]

[[tester.tests]]
cwd = "apps/web"
command = "npm test"
timeout = 300
gate = true

[[tester.dev_server]]
cwd = "apps/web"
command = "npm run dev"
ready_url = "http://127.0.0.1:3000/health"
startup_timeout = 30

[[tester.mcp]]
name = "browser"
command = "npx"
args = ["PROJECT_CHOSEN_MCP_PACKAGE"]
```

Test command timeout defaults to 300 seconds; dev-server readiness defaults
to 30 seconds. Relative command `cwd` paths must exist within the active
checkout/worktree. `env` may hold non-secret overrides; keep credentials in
the process environment. `meow native verify` reports local launcher and
configuration readiness without running tests, servers, agents, or MCP calls;
`ready_unchecked` does not mean the command or connection has succeeded.

Native `/meow:run` and `/meow:review` use the CLI flow when `--test` is
requested so MEOW can own server lifecycle during tester work. Normal native
behavior is unchanged when tester mode is off.

Use the project's normal agent instruction files for persistent guidance;
`docs/ARCHITECTURE.md` describes architecture.

## Reviewer architecture check

Every MEOW reviewer performs a language-agnostic architecture pass in addition
to correctness and configured gates. It checks both individual files and the
module/package layout for mixed responsibilities, catch-all modules, and groups
of unrelated files that make ownership or navigation unclear. The check uses
cohesion, dependency direction, discoverability, and change patterns as
evidence; it does not impose a universal file-count or line-count threshold.

The reviewer must cite the affected files and suggest a responsibility-based
split. A layout concern is blocking only when it affects the reviewed feature
or clearly makes maintenance unsafe; otherwise it is recorded as an advisory
follow-up. This keeps the check useful across Python, TypeScript, and other
languages without forcing mechanical refactors.

---

## `[jira]` / `[jira.mcp]` — for `meow run --jira` / `meow review --jira`

| Field | Required | Default | Notes |
|---|---|---|---|
| `[jira].project_key` | Only for `--jira` | — | Jira project searched for "the latest issue" when no issue key is given. |
| `[jira].branch_prefix` | No | `"issue/"` | Prefix for the branch `meow run --jira` creates and pushes (build mode only — `meow review --jira` never creates a branch). |
| `[jira.mcp].command` | Only for `--jira` | — | Program that launches the Jira MCP server, e.g. `"uvx"`. |
| `[jira.mcp].args` | No | `[]` | Its arguments, e.g. `["mcp-atlassian"]`. |

Its server reads its own credentials from the environment (`JIRA_URL` plus
either `JIRA_USERNAME`+`JIRA_API_TOKEN` for Cloud or `JIRA_PERSONAL_TOKEN` for
Server/Data Center) — never put them in the shared `.meow/config.toml`.

## `[gitlab.mcp]` — for `meow review --gitlab`

Unlike `[jira]`, there's no `project_key`-style field — the merge request URL
is passed on the command line each time.

| Field | Required | Default | Notes |
|---|---|---|---|
| `[gitlab.mcp].command` | Only for `--gitlab` | — | Program that launches a GitLab MCP server exposing merge-request read tools. |
| `[gitlab.mcp].args` | No | `[]` | Its arguments. |
| `[gitlab.mcp].env` | No | `{}` | Environment variables passed to the launched server, e.g. `GITLAB_URL`, `GITLAB_TOKEN`. |

**Security note:** `[gitlab.mcp].env` is passed to the launched server as-is.
Keep tokens in the process environment or in the ignored
`.meow/config.local.toml`; do not commit them to the shared
`.meow/config.toml`. MEOW expands `$NAME`, `${NAME}`, and `%NAME%` in string
configuration values without evaluating them as shell code.

## `[github.mcp]` — for `meow review --github`

Unlike `[jira]`, there's no project field — the pull request URL is passed on
the command line each time.

| Field | Required | Default | Notes |
|---|---|---|---|
| `[github.mcp].command` | Only for `--github` | — | Program that launches a GitHub MCP server exposing pull-request read tools. |
| `[github.mcp].args` | No | `[]` | Its arguments. |
| `[github.mcp].env` | No | `{}` | Environment variables passed to the launched server, e.g. `GITHUB_PERSONAL_ACCESS_TOKEN`. |

The configured MCP server must expose the GitHub tools needed to fetch a pull
request's title, body, and unified diff. MEOW accepts any compatible server.

**Security note:** `[github.mcp].env` is passed to the launched server as-is.
Keep tokens in the process environment or in the ignored
`.meow/config.local.toml`; do not commit them to the shared
`.meow/config.toml`. MEOW expands `$NAME`, `${NAME}`, and `%NAME%` in string
configuration values without evaluating them as shell code.

---

## Running `meow run --jira` on a schedule

`meow run --jira [ISSUE-KEY]` fetches a Jira issue (or the most recently
created one in `[jira].project_key` if you omit the key), solves it through
the same plan/implement/review loop as a plain `meow run`, inside its own
worktree on branch `<branch_prefix><ISSUE-KEY>` (default
`issue/<ISSUE-KEY>`), then pushes that branch to `origin`. On success it
prints one JSON line to stdout — `{"issue": "PROJ-123", "branch":
"issue/PROJ-123"}` — and exits 0; any failure (no active Jira MCP, no
matching issue, the sprint not passing within `max_rounds`, or the push
failing) raises before that line is printed, and the process exits non-zero.

**Never pass `--manually-approve-plan`/`-m` on a scheduled run** — it prompts
on stdin for approval before the generator starts, and a scheduled task has no
console attached to answer it, so the run just hangs instead of completing or
failing cleanly.

**Prerequisites**: `[jira]`/`[jira.mcp]` set in `.meow/config.toml` (above), a
Jira MCP server reachable with those settings (this repo assumes
[`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian), installable
with `uvx` so no separate install step is needed), its credentials in the
environment, and an `origin` remote the scheduled task's account can push to
(e.g. an SSH key or stored credential, not an interactive prompt).

**Persistent logs**: a scheduled task has no attached console, so set
`MEOW_LOG_FILE` to a path before running — every run appends its key=value log
lines there instead of only writing to stderr (`MEOW_LOG_LEVEL` also works the
same way `meow run` uses it, e.g. `DEBUG` for more detail).

MEOW does not include its own operating-system scheduler. Use the scheduler
that fits the machine or CI environment that will run the command.

**Windows Task Scheduler** — from an elevated PowerShell prompt, using
`schtasks` (adjust the venv path, working directory, issue key or omit it for
"latest", and schedule):

```powershell
schtasks /Create /TN "meow-run-jira" /SC DAILY /ST 09:00 /RL LIMITED /TR (
    '"C:\path\to\meow\.venv\Scripts\meow.exe" run --jira' +
    ' --work-dir "C:\path\to\target-project"'
)
```

`schtasks /TR` runs with a minimal environment, so set `MEOW_LOG_FILE` and the
Jira credentials as that account's persistent user/system environment
variables (`setx`) rather than relying on variables set in your interactive
shell. Verify the task once with `schtasks /Run /TN "meow-run-jira"`, then
`Get-Content <MEOW_LOG_FILE> -Tail 50` to confirm it ran and to read its
result.

**Linux cron** — add a crontab entry for the account that has Jira credentials
and push access. Use absolute paths because cron starts with a minimal
environment:

```cron
MEOW_LOG_FILE=/var/log/meow-run-jira.log
JIRA_URL=https://jira.example.com
JIRA_USERNAME=automation@example.com
JIRA_API_TOKEN=...

0 9 * * * /path/to/meow/.venv/bin/meow run --jira --work-dir /path/to/target-project
```

**Linux systemd timer** — prefer this when you want journal logs, explicit
environment files, or easier enable/disable controls:

```ini
# /etc/systemd/system/meow-run-jira.service
[Service]
Type=oneshot
Environment=MEOW_LOG_FILE=/var/log/meow-run-jira.log
EnvironmentFile=/etc/meow/jira.env
ExecStart=/path/to/meow/.venv/bin/meow run --jira --work-dir /path/to/target-project
```

```ini
# /etc/systemd/system/meow-run-jira.timer
[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and test it with `systemctl enable --now meow-run-jira.timer`, then
`systemctl start meow-run-jira.service` and inspect
`journalctl -u meow-run-jira.service`.

---

## If something's missing

| Missing / wrong | Result |
|---|---|
| `.meow/config.toml` absent | Built-in defaults are used; no lint commands are configured. |
| A lint field is present but no `[[lint]]` entry is defined | `ValueError`: no lint command defined. Config files containing only models, skills, MCP, or other settings are valid. |
| Unknown key in a `[[lint]]` table (often a top-level key placed after it) | `ValueError` naming the entry and key |
| No architecture doc anywhere under `docs/` | No error — reviewer's SOLID/SRP pass finds nothing to Glob/Read, so it has no project-specific boundaries to check, just its generic mixed-responsibility rule |
| `AGENTS.md` | No effect on meow — human-facing only |
| `meow run --jira` run without `[jira]`/`[jira.mcp]` | `ValueError` naming the missing table/key, before any agent runs |
| `meow run --jira` run with no Jira MCP actually reachable | `RuntimeError` from the preflight check — it requires an actual `mcp__jira__*` tool call to succeed, not just a text claim of success |
| `meow run --jira` run with no `origin` remote | `RuntimeError` after the sprint passes, before attempting to push |
| `meow review --gitlab` run without `[gitlab]`/`[gitlab.mcp]` | `ValueError` naming the missing table/key, before any agent runs |
| `meow review --gitlab` run with no GitLab MCP actually reachable | `RuntimeError` from the preflight check — it requires an actual `mcp__gitlab__*` tool call to succeed, not just a text claim of success |
| `meow review --gitlab --fix` (both given together) | `ValueError` rejecting the combination before fetching anything — there is no local checkout of a merge request to fix |
| `meow review --github` run without `[github]`/`[github.mcp]` | `ValueError` naming the missing table/key, before any agent runs |
| `meow review --github` run with no GitHub MCP actually reachable | `RuntimeError` from the preflight check — it requires an actual `mcp__github__*` tool call to succeed, not just a text claim of success |
| `meow review --github --fix` (both given together) | `ValueError` rejecting the combination before fetching anything — there is no local checkout of a pull request to fix |
| `meow run --lint-fix` (standalone, not `--report-only`) never gets lint clean within `max_rounds` | `LintFixError` including the still-failing commands' raw output |
| `meow review --review-file` pointing at a GitLab MR review file | `RuntimeError` explaining there is no local checkout of the merge request's code to fix — re-run with `--gitlab` instead |
| `meow review --review-file` pointing at a GitHub PR review file | `RuntimeError` explaining there is no local checkout of the pull request's code to fix — re-run with `--github` instead |
| `meow review --review-file` pointing at a branch review file | `RuntimeError` explaining it can't be resumed this way (no target branch to re-diff against) — re-run with `--fix --branch <branch> --target <target>` instead |
| `meow review --review-file` omitted and no review file anywhere in `docs_dir` | `FileNotFoundError` naming `docs_dir` and pointing at `--review-file` |
| `meow review --fix` (any source) never passes within `max_rounds` | `RuntimeError` naming the review file, same stop/raise shape regardless of source |

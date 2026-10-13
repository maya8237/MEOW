# docs/INTEGRATIONS.md — Jira, GitLab, GitHub, and scheduled runs

Reference material for optional project setup. MEOW sets up a project's base
configuration itself on the first config-dependent command; run `/meow:onboard`
for the add-ons below. This guide covers monorepo lint/test configuration,
tester mode, build/worktree/permission policies, integrations, scheduled Jira
runs, and error messages.

---

## Monorepo lint and tester

`.meow/config.toml` may be absent before the first config-dependent command. In
that case MEOW starts with built-in defaults, auto-creates the base config when
the command onboards the project, and has no lint commands until one is
configured. Configuration priority is the ignored local
`.meow/config.local.toml`, project `.meow/config.toml`, user
`~/.meow/config.toml`, then built-in defaults. String values may reference
environment variables with either POSIX syntax (`$HOME` or `${HOME}`) or
Windows syntax (`%USERPROFILE%`); expansion is applied recursively to
configured tables and lists. Unknown variables are left unchanged.

When a run creates a feature worktree, it normally reads configuration from
the original project root, including `.meow/config.local.toml`; the worktree
does not need its own copy. The exception is a project with no shared
`.meow/config.toml`: if auto-onboarding creates that file in the worktree,
the run reads configuration from the worktree until the shared config is
available in the original project root.

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
# `architecture_files` is optional; the default is docs/ARCHITECTURE.md.
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

## Other project configuration

The same layered config can enable the following optional capabilities. Keep
credentials and machine-specific values in `.meow/config.local.toml` or the
user config.

### Build gates

Each `[[build]]` entry has the same command fields as a test (`command`,
optional `args`, `cwd`, `env`, and `timeout`) plus `required` (default `true`).
Required builds must pass before delivery; an advisory entry with
`required = false` is recorded without blocking completion. Build commands are
launched as argument vectors, not through shell evaluation, and the timeout
defaults to 300 seconds.

### New-worktree setup

`[worktree_setup]` runs when MEOW creates a new feature, issue, branch-review,
or task worktree, in SDK and native mode (including plan-only runs):

```toml
[worktree_setup]
copy = [".env.example"]
commands = [["python", "-m", "pip", "install", "-e", "."]]
```

`copy` entries are regular, non-secret-like relative files copied from the
project checkout. `commands` are literal argument arrays, run in the new
worktree with a 300-second timeout. Paths containing `..`, absolute paths,
links, or secret-like names are rejected. Onboarding previews each command;
the `setup` permission role can deny or require approval. An approval request
stops an unattended run rather than prompting indefinitely.

Failed setup retains its worktree and a `.meow/worktree-setup.pending` marker.
Preparing that worktree again retries setup; successful setup is not repeated
on ordinary reuse. Copies already completed are accepted when their bytes match.

After explicit setup, MEOW installs missing Node dependencies before running
agents. This also repairs reused worktrees and in-place runs. It detects nested
apps and installs workspace members together from their workspace root, without
sharing a writable `node_modules` directory between branches. `packageManager`
in `package.json` takes priority over lockfiles; ambiguous lockfiles require an
explicit manager. Supported managers are Bun, npm, pnpm, and Yarn (including PnP).
Existing lockfiles use frozen/immutable installs (`npm ci` for npm). Projects
without a lockfile use a regular install. Automatic commands obey the same
`setup` permission role, retain installation diagnostics, and fail clearly when
the required manager is missing from PATH.

The installer offers a default-yes Bun shared-store option. It preserves other
user settings while setting `linker = "isolated"` and `globalStore = true` in
`~/.bunfig.toml`; Bun's default cache determines the shared store location.
`globalDir` controls global CLI packages and is not needed for this feature.
Previously registered and newly onboarded Bun repos are migrated automatically
in a detached background process, including nested apps such as `frontend/`.
The installer prints the migration PID and log path; migration may continue
after the installer prints Done. `bun install --force --linker isolated` rebuilds
existing dependencies (`--frozen-lockfile` is added when a Bun lockfile exists).
Configuration is user-only: project `bunfig.toml` files are never created or
edited. Existing project configurations produce a warning in the migration log
because they can override user settings. Migration failures include a retry
command. No package-manager switch is performed for npm/pnpm/Yarn.
Use a Bun release supporting `install.globalStore`; project settings and
environment variables can override the user configuration.

For additional Bun repos, run from the install root:

```sh
bun install --force --linker isolated --frozen-lockfile
```

Omit `--frozen-lockfile` if the repo has no Bun lockfile. Shared storage still
requires a local install to create each worktree's links. With the option off,
MEOW uses the package manager's normal cache/install behavior instead. See
[Bun's global store documentation](https://bun.com/docs/pm/global-store).

### Role permissions

Use repeated `[[permissions.rule]]` tables to constrain a role's tool use:

```toml
[[permissions.rule]]
role = "generator"
tool = "Bash"
action = "deny"
```

`role` names the MEOW role, `tool` names an SDK tool, and `action` is
`allow`, `deny`, or `ask`. A `path` can scope `Read`, `Write`, `Edit`, or
`NotebookEdit` rules to a repository-relative path. Path-scoped rules require
both `Bash` and `Agent` to be denied for that role; path values cannot escape
the project. In unattended mode, an `ask` decision stops the run.

### Browser tester providers

`[tester.browser]` requires `kind` (`skill`, `mcp`, or `command`), `name`, and
`entrypoint`; `required` defaults to `false`. Provider-specific `inputs`,
`outputs`, and `permissions` may be tables or lists. A command provider may
also set `args`, `cwd`, `env`, `timeout`, named `flows`, and repository-relative
`artifacts`. For command providers, configure `[[tester.dev_server]]` with a
`ready_url` when the application needs a local server. The server is started
for the check and stopped afterward. Browser evidence is shown separately in
`meow status`; keep generated artifacts under `.meow/` so verification does
not dirty the feature revision.

### Agent skills and delivery defaults

`[agent_skills]` adds installed skill identifiers to every role or to a named
role. `default` and role-specific lists append across config layers; built-in
MEOW skills come from each resolved agent definition and can be replaced
by an override. Explicit `[models].role` values win over agent models. `[delivery].target_branch` (default `dev`) is the
branch `meow review --ci` reviews against when `--target-ref` is omitted.

### Custom skills and agents

`[custom]` points MEOW at directories of your own skills and agent definitions.
They are discovered every time configuration loads, so adding, editing, or
deleting a file takes effect on the next run without reinstalling anything.

```toml
[custom]
# Each skills directory holds <name>/SKILL.md (Claude Code skill format).
skills = ["tools/meow/skills"]
# Each agents directory holds <name>.toml (.md remains an import format).
agents = [{ path = "tools/meow/agents", roles = ["generator"] }]
workflows = ["tools/meow/workflows"]
```

An entry is a path string or a table with `path` and optional `roles` for
skills and agents. Workflow sources accept `path` only. Set `[workflow].default`
to choose a named workflow when `meow run --workflow` is omitted; the default
is `"default"`. See [WORKFLOWS.md](WORKFLOWS.md) for formats and execution rules.

Conventional `.meow/agents/` and `.meow/workflows/`, `.meow/local/agents/` and
`.meow/local/workflows/`, and `~/.meow/agents/` and `~/.meow/workflows/` need no
registration. Skills retain their configured-source discovery.

| Scope | Declared in | Paths | Shared with |
|---|---|---|---|
| Local project | `.meow/config.local.toml` (git-ignored) | relative to the project root, absolute, or `~/...` | only you, in this project |
| Project | `.meow/config.toml` | relative, inside the repository, and **not** git-ignored | everyone who clones the repository |
| User | `~/.meow/config.toml` | absolute, `~/...`, or relative to `~/.meow` | every project on this computer |

The directory can be anywhere you choose within those rules (for example
`tools/meow/`, `docs/agents/`, or `~/dotfiles/meow-skills`). A project-scope
path that escapes the repository or is git-ignored is rejected, because it
would not reach your collaborators; declare it in the local config instead.
Project-scope custom folders resolve from the launch checkout, even when MEOW
runs in a feature worktree based on another branch.

A missing user-scope folder logs a `custom_source_missing` warning and is
skipped. `meow native custom` reports it under the `skipped` key. Missing project
or local folders still raise an error.

**Precedence.** When the same skill or agent name appears in more than one
scope, local project beats project, which beats user, which beats builtin;
whole definitions replace lower-priority files. `meow native custom`
lists each override. The same name twice within one scope is an error.

**Skills.** `SKILL.md` needs `name` (matching its directory; lowercase letters,
digits, and hyphens) and `description`; supporting files beside it are kept.
Without `roles`, a skill is enabled for every role; with it, only for the
listed roles (`explorer`, `planner`, `generator`, `reviewer`, `tester`,
`review_fixer`, `lint_fixer`, `docs_updater`, `issue_fetcher`,
`gitlab_fetcher`, `github_fetcher`). In CLI mode MEOW serves them to the Agent
SDK as a generated local plugin, so roles see them as `meow-custom:<name>`;
built-in and `[agent_skills]` skills stay enabled alongside them.

**Agents.** TOML definitions set `description`, `prompt`, `model`, `tools`,
`skills`, `agents`, `session`, and `lint_on_edit`, with optional `variants` and
`fragments`. A name maps to its config/permission role by replacing hyphens
with underscores. `explorer` can be overridden. Claude Code `.md` frontmatter
and prompt bodies remain an import format; unrecognized Claude Code keys are
ignored. Specify tools for a standalone workflow role.

Configured `[custom].agents` retain delegate compatibility with planner and
generator (or their configured roles). Conventional agent definitions are used
when explicitly named by a workflow or another agent's `agents` list.

**Permission boundary.** A workflow role uses its resolved tools, then the
role's `[permissions]` policy. Delegates are additionally capped to their
parent's tools and cannot use `Agent`; dropped tools log
`custom_agent_tools_dropped`. A prompt or skill does not grant shell, network,
or MCP access. Discovered agent names are accepted as permission roles;
unknown roles remain configuration errors.

Run `meow agents list`, `meow agents show NAME`, or `meow workflows validate`
to inspect resolved definitions. `meow agents eject NAME --scope project`
copies a shipped definition for editing; `meow agents diff NAME` compares it
with the shipped version. `meow native custom [--role ROLE]` shows compatibility
delegates and custom skills; `meow native verify` validates configuration.
`/meow:customize` guides definition editing and registration.

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

## `[orchestrator]` — for `meow orchestrate`

Every key is optional. These are the defaults:

```toml
[orchestrator]
max_parallel = 3
max_recovery_cycles = 3
review_rounds = 3
branch_prefix = "orchestrator/"
poll_seconds = 5
open_pr = true
```

| Field | Default | Notes |
|---|---|---|
| `max_parallel` | `3` | Tasks running at once (integer, at least 1). `--max-parallel` overrides it for one run. |
| `max_recovery_cycles` | `3` | Triage-and-retry cycles one task may use before the orchestrator blocks (integer, at least 0). A task split into subtasks passes its counter on to them. |
| `review_rounds` | `3` | Review/fix rounds of the whole-objective review before it blocks (integer, at least 1). |
| `branch_prefix` | `"orchestrator/"` | Prefix of the integration branch (`<prefix>ID`) and the task branches (`<prefix>ID-TASK`). Non-empty, no spaces, no `..`, no leading `-`. |
| `poll_seconds` | `5` | Seconds between checks of the child runs while `meow orchestrate` drives (greater than 0). |
| `open_pr` | `true` | Open a pull or merge request after pushing. `--no-pr` turns it off for one run. |

Unknown keys and values of the wrong type are rejected when the config loads.
`[models]` also accepts `decomposer`, `triage`, `integrator`, and
`pull_request` to choose a model per orchestrator role.

**Jira input.** `--jira KEY ...` and `--jql "..."` need `[jira]` (with
`project_key`) and `[jira.mcp]` exactly as described above. The orchestrator
only reads Jira: it never comments on or transitions an issue. In native mode
(`/meow:orchestrate`) the session's connected Jira tools do the fetching
instead.

**Pull request creation.** After pushing, the orchestrator opens a pull or merge
request from the integration branch into the base branch. The provider is
chosen from the host of the `origin` remote: `github.com`, or any host
containing `github`, is GitHub; a host containing `gitlab`, or equal to the host
of `[gitlab.mcp].env.GITLAB_URL`, is GitLab; any other host has no provider. The
chosen provider must also have its `[github.mcp]` or `[gitlab.mcp]` table,
otherwise the request is skipped with "provider not configured". The configured
MCP server must expose a tool whose name contains `create_pull_request`
(GitHub) or `create_merge_request` (GitLab). The pull-request agent may call
only tools whose names contain `create_pull_request`, `create_merge_request`,
`list_pull_requests`, `list_merge_requests`, or `search_pull_requests` (the
last three let it reuse an open request for the branch), and it writes only its
result file. A missing provider or a failed creation never undoes the push: the
orchestrator ends `delivered`, `meow orchestrate` exits with code 3, and it
prints `PR not opened: REASON` with a manual link. Native mode opens the request
with the GitHub or GitLab tools already connected to the session.

---

## Scheduling unattended runs

MEOW registers one-shot runs with Windows Task Scheduler or Linux systemd user
timers. The CLI, `/meow:schedule` skill, and UI use the same scheduling helper:

```bash
meow schedule add --at "09:00" --work-dir /path/to/project -- run --jira PROJ-123
meow schedule add --at "2026-12-01T09:00:00+02:00" -- run "Add CSV export" --name csv-export
meow schedule list --work-dir /path/to/project
meow schedule cancel <schedule-id> --work-dir /path/to/project
```

`HH:MM` means the next occurrence in the machine's local timezone; ISO
timestamps can specify an explicit offset. Each schedule runs once. Scheduling
adds `--unattended`, validates the arguments, and rejects interactive approval,
lint-fix, background mode, source-branch selection, and disabling worktree
isolation. The normal plan, implementation, verification, review, and delivery
policy applies when the run starts. See [CLI.md](CLI.md#scheduling-a-run).

In a plugin conversation, ask MEOW to run a request or Jira issue at a future
time, or invoke `/meow:schedule`. In the UI, select **Schedule for later** in
the session launcher, choose a local date/time, preview, and schedule. UI
schedules save the reviewed recipe, so later edits do not change that run.
The session details show its scheduled time and cancellation control.

**Windows:** tasks run as the current logged-in user with least privilege.
No administrator privileges or password storage are needed. Keep that user
logged in for execution. **Linux:** `systemctl --user` must connect to a working
systemd user manager; enable lingering separately if execution must work while
logged out. Persistent timers and Windows's StartWhenAvailable setting allow
missed times to run when the scheduler becomes available. The machine must be
available, and the registered project and Python environment must stay in place.

Schedule metadata and CLI output logs live under `.meow/schedules/`; normal run
journals still live under `.meow/runs/`. UI execution creates a session attempt
whose output and result appear in the UI. Cancellation removes the trigger;
use the normal run/session stop controls if execution has already started.
Registration failures are reported immediately and saved with state `failed`.
Repeated invocation of a consumed trigger does not rerun its request.

**Jira prerequisites:** configure `[jira]`/`[jira.mcp]`, provide a reachable
Jira MCP server, and make its credentials available to the scheduler's user.
`run --jira` without an issue key selects the latest configured project issue
when the run executes. The scheduler must also have noninteractive Claude and
Git credentials where required by the selected workflow and delivery policy.
MEOW preserves the registration-time executable search path; shell-only secret
environment variables are not copied into schedule files. Set credentials in
the account's persistent environment or the integration's supported credential
configuration. `MEOW_LOG_FILE` can provide an additional persistent log.

For recurring jobs, an external recurring Task Scheduler, cron, systemd, or CI
configuration can invoke `meow run --unattended` with absolute executable and
project paths. MEOW's built-in schedules are one-shot.

---

## If something's missing

| Missing / wrong | Result |
|---|---|
| `.meow/config.toml` absent before first config-dependent use | Built-in defaults guide automatic setup; the command creates the base config when onboarding applies. Until a lint entry is configured, no lint commands run. |
| A lint field is present but no `[[lint]]` entry is defined | `ValueError`: no lint command defined. Config files containing only models, skills, MCP, or other settings are valid. |
| Unknown key in a `[[lint]]` table (often a top-level key placed after it) | `ValueError` naming the entry and key |
| No architecture doc anywhere under `docs/` | No error — reviewer's SOLID/SRP pass finds nothing to Glob/Read, so it has no project-specific boundaries to check, just its generic mixed-responsibility rule |
| `AGENTS.md` | Read as human-facing project guidance during the pre-plan audit; it does not replace `.meow/config.toml` or change config parsing. |
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
| `meow orchestrate --jira` or `--jql` run without `[jira]`/`[jira.mcp]` | Exit code 1 naming the missing table or key, before any worktree is created |
| `meow orchestrate` run with uncommitted changes | Refused with exit code 1, the same rule as `meow run` |
| `meow orchestrate` cannot push the integration branch | The orchestrator is `failed` with the git error and nothing is lost; fix `origin`, then `meow orchestrate resume ID` retries delivery |
| `meow orchestrate` finds no configured GitHub or GitLab provider, or opening the request fails | Still `delivered`; exit code 3 with `PR not opened: REASON` and a manual link |

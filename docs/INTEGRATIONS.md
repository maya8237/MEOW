# Configuration and integrations

First config-dependent use creates base project configuration; `/meow:onboard`
configures optional features. Use [the template](../templates/meow-config.toml.example)
for fields, [custom sources](CUSTOM.md) for discovery, and
[worktree setup](WORKTREE_SETUP.md) for dependency installation.

## Monorepo lint and tester

Priority: ignored `.meow/config.local.toml` > shared `.meow/config.toml` >
`~/.meow/config.toml` > defaults. Worktrees normally read the launch project's
config, including local values. Onboarding creates missing shared config there;
its edits stay outside feature/task branches. Without lint entries, none run.

String values recursively expand `$NAME`, `${NAME}`, or `%NAME%` without shell
evaluation; unknown variables remain visible. Keep credentials/machine settings
in local/user config or the environment, never shared config.

`[[lint]]` accepts `command`, `args`, `cwd`, `include`, `exclude`, `env`,
`timeout`, and `gate` (default true). Include/exclude are repository-relative
prefixes. Per-file lint respects cwd/prefixes; project-wide runs every entry.
Timeout is per-entry or `lint_timeout` (default 60 seconds). [Hook evidence and test policy](VERIFICATION.md).

`meow run --test` enables tester validation after passing review; `--force-disable-tests` overrides it.
`meow review --plan PATH --test` is report-only unless `--fix` is added;
other review sources and review-file reject tester mode.
Without configured commands the tester may inspect/run documented tests.
`[tester] write_tests = true` (default false) lets the tester add new test
files for the change; it still may not edit or delete existing files.
Native tester flows route through CLI for server lifecycle.

```toml
[models]
tester = "haiku"
[tester]
test_dirs = ["apps/web/tests", "services/api/tests"]
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

Test timeout defaults to 300 seconds, readiness to 30 seconds, and architecture
context to `docs/ARCHITECTURE.md`. Relative command cwd must exist inside the
active checkout. `meow native verify` checks configuration/launcher readiness
without starting tests, servers, agents, or MCP; `ready_unchecked` proves no
execution or connectivity.

## Build gates

`[[build]]` uses test command fields (`command`, optional `args`, `cwd`,
`env`, `timeout`) plus `required` (default true). Required failures block
delivery; advisory failures are recorded. Commands are argv, without shell
evaluation; timeout defaults to 300 seconds.

## New-worktree setup

See [WORKTREE_SETUP.md](WORKTREE_SETUP.md) for `[worktree_setup]`, setup-role
permissions, retained failures, Node package managers, and Bun shared storage.
This applies to SDK/native preparation, reused worktrees, and in-place dependency repair.

## Role permissions

```toml
[[permissions.rule]]
role = "generator"
tool = "Bash"
action = "deny"
```

Actions are `allow`, `deny`, or `ask`; runs stop on `ask`. Repository-relative
`path` scopes Read/Write/Edit/NotebookEdit and must not escape the project.
Path-scoped roles must deny both Bash and Agent.

## Browser tester providers

`[tester.browser]` requires `kind` (`skill`, `mcp`, `command`), `name`,
and `entrypoint`; `required` defaults false. Provider `inputs`, `outputs`,
and `permissions` can be tables/lists. Commands also accept `args`, `cwd`,
`env`, `timeout`, named `flows`, and repository-relative `artifacts`.
Configure a dev server/readiness URL when needed; MEOW starts it for browser
checks and stops it afterward. Required failures/unavailable providers block
completion. Status separates lint/test/build/browser evidence. Keep generated
artifacts under `.meow/` so checks do not alter the reviewed revision.

## Agent skills and delivery defaults

`[agent_skills].default` and role lists append across config layers to resolved
definition/custom skills. Agent overrides may replace built-in skill lists.
Explicit `[models].role` wins over agent models.
`[delivery].target_branch` selects the default PR/MR target; `meow run --target`
overrides it per run. Auto-onboarding saves existing `dev` (local or origin),
otherwise `master`. It also supplies the CI target unless `--target-ref` overrides it.

## Custom skills and agents

Read [CUSTOM.md](CUSTOM.md) for `[custom]` paths, roles, scopes, validation,
and permission boundaries; [WORKFLOWS.md](WORKFLOWS.md) owns definition formats.
Custom sources reload each run without reinstalling.

## Reviewer architecture check

Reviewers inspect correctness, configured gates, and responsibility/dependency
boundaries without universal file/line thresholds. Findings cite files/evidence
and propose a responsibility-based split. Block only feature-relevant or
clearly unsafe maintenance issues; otherwise record an advisory follow-up.
`AGENTS.md` is project guidance, not configuration.

## Jira

`[jira].project_key` is required for Jira flows; omitted issue keys select
the latest issue in that project. `branch_prefix` defaults to `"issue/"`
for builds; reviews create no branch.
`[jira.mcp].command` launches the CLI server (for example `uvx`);
`args` defaults to `[]` (for example `["mcp-atlassian"]`).

Credentials are `JIRA_URL` and either Cloud `JIRA_USERNAME`/`JIRA_API_TOKEN`
or Server/Data Center `JIRA_PERSONAL_TOKEN`. Native skills use session-connected tools.

## GitLab and GitHub MCP

`[gitlab.mcp]` and `[github.mcp]` require `command` for their CLI flows;
optional `args = []` and `env = {}` configure their launchers.
The URL is supplied per review; there is no provider project-key setting.
Servers must expose title/description/unified-diff read tools.
MCP env is passed to the server as configured: keep tokens private.
Typical variables: GitLab `GITLAB_URL`/`GITLAB_TOKEN`,
GitHub `GITHUB_PERSONAL_ACCESS_TOKEN`. Native skills use connected tools instead.

## Orchestrator

Every `[orchestrator]` key is optional:

```toml
[orchestrator]
max_parallel = 3
max_recovery_cycles = 3
review_rounds = 3
branch_prefix = "orchestrator/"
poll_seconds = 5
open_pr = true
```

Concurrency/review rounds are integers ≥1, recovery cycles ≥0 (shared by split
lineage), and polling seconds >0. Prefixes must be nonempty, without spaces,
`..`, or leading `-`; integration uses `<prefix>ID`, tasks `<prefix>ID-TASK`.
Wrong types/unknown keys fail config validation. CLI `--max-parallel` and
`--no-pr` override defaults; models can set `decomposer`, `triage`,
`integrator`, and `pull_request`. Jira inputs require project/server config
and never comment on or transition issues.

PR provider comes from origin: hosts containing `github` select GitHub;
`gitlab` or the host in `[gitlab.mcp].env.GITLAB_URL` selects GitLab.
Other hosts have no provider. Missing provider MCP config reports
`provider not configured`. PR tools are restricted to names containing
`create_pull_request`, `create_merge_request`, `list_pull_requests`,
`list_merge_requests`, or `search_pull_requests`; existing open requests
can be reused. The agent writes only its result file.
Creation failure never undoes a push: delivered state, exit 3,
`PR not opened: REASON`, and a manual link. Native uses connected tools.

## Scheduling automatic runs

[CLI scheduling](CLI.md#scheduling-a-run) covers commands and OS requirements.
Jira schedules need reachable project/MCP configuration and scheduler-user
credentials; an omitted key selects the latest issue when execution begins.
Claude/Git credentials must support noninteractive execution. Registration
preserves executable PATH but does not copy shell-only secrets. Use persistent
account/integration credentials; `MEOW_LOG_FILE` adds a persistent log.
For recurrence, external Task Scheduler/cron/systemd/CI can invoke
`meow run` with absolute executable/project paths; built-in triggers are one-shot.

## If something's missing

| Condition | Result |
|---|---|
| No config before first use | Base onboarding/defaults; no lint until configured |
| Lint fields without entries, or unknown lint keys | ValueError naming the problem; model/skill/MCP-only config is valid |
| No architecture doc | Optional context; generic architecture checks still apply |
| Missing Jira/GitLab/GitHub config | ValueError before agents |
| Unreachable MCP | RuntimeError; preflight requires a real provider tool call |
| Remote `--fix`, or remote/branch review-file resume | Rejected; check out locally or rerun the original source |
| Missing review file | FileNotFoundError naming docs_dir and review-file |
| Exhausted fix rounds | LintFixError with output, or review RuntimeError naming report |
| Jira delivery without origin | RuntimeError after PASS; evidence/worktree retained |
| Orchestrator dirty start or missing Jira config | Exit 1 before work starts |
| Orchestrator push failure | Failed state; repair origin and resume delivery |
| Missing PR provider/creation failure | Delivered, exit 3, manual PR link |

# CLI guide

`meow review --scope project [--fix]` inventories and reviews the whole
checkout, including frontend and backend areas, even with pending changes or
an existing plan. Prompt/Jira reviews also accept `--scope diff` or `auto`
(default). Whole-project wording such as "from 0" selects project coverage
automatically. Project verdicts require grounded `COVERAGE:` records for every
inventoried area; missing, fabricated, or stale records force FAIL.

Local CLI reviews run configured required lint, tests, and builds before
accepting PASS; successful checks are reused only while inputs remain current.
Remote PR/MR reviews use their supplied diff and do not run local checks.
For native execution, `meow native review-checks --background` starts durable
verification and `--status` reports its state and log/result paths. Native
verdicts enforce these checks and scope coverage in code. Native prompts accept
`--host codex` (or another host) and repeated `--available-model ID` options;
an unsupported configured model yields `model: null` to inherit the host model,
with the configured model and fallback reason preserved in JSON.

Native `custom` and `orchestrate review` accept the same host/model capability
flags as `prompt`. A resumed PASS is reviewed again when a new focus is given;
whole-project wording widens an earlier diff review to project scope.

This page covers the detailed behavior of MEOW's command-line interface.
For installation and a quick start, see the [README](../README.md). For Jira
and GitLab/GitHub configuration, scheduled runs, and error details, see
[INTEGRATIONS.md](INTEGRATIONS.md).

## User commands

Run `meow --update` or `meow -u` (also available through `mw`) from any
directory to install the latest published stable GitHub Release. Installation,
updates, and version warnings use release tags, never `main` or its latest
commit. Equal or newer installed versions are left alone. `--update` cannot
be combined with a project command; there is no `--upgrade` option.

Release archives include Python, MEOW's dependencies, the CLI launcher, the
local UI, built-in definitions, and Claude plugin files. The installer verifies
the archive's SHA-256 checksum and installs under `~/.meow/versions/<tag>`
(`%USERPROFILE%\\.meow` on Windows). `~/.meow/bin` contains the stable `meow`
and `mw` launchers and is added to the user's `PATH`. Configuration, caches,
and registered projects are preserved. The plugin is registered before the
launcher switches to the new release. Previous versions remain available to
running workers and existing scheduled jobs; do not delete versions still in
use. Updates return nonzero on download, checksum, or activation failure.

No system Python, pip, or MEOW source clone is required. Git and your project
tools are still needed for workflows that use them. Initial installation
offers the existing Bun and project onboarding prompts; updates preserve
settings without prompting again. Rerun the install script to repeat setup
or migrate an old editable installation. The installer puts `~/.meow/bin`
first in the user PATH; remove obsolete MEOW launchers from an active virtual
environment if they take precedence in your current shell.

For unattended feature execution use `meow run --auto` or `meow run -a`.
`-u` is reserved for updating
MEOW and is used before the command, as `meow -u`.

Every public CLI invocation checks for newer versions and warns on stderr
with `meow --update` when one is available. This best-effort check has a short
network timeout and stays silent on failure. Native JSON helpers and internal
workers skip it. Each public `/meow:*` skill checks once at entry, including
native-mode skills. `meow --check-update` performs only this warning check.

These are the commands meant for normal project work:

| Command | Use it when you want to |
| --- | --- |
| `meow run` | Plan, implement, verify, review, and optionally deliver a feature request. |
| `meow review` | Review existing code, and optionally fix findings with `--fix`. |
| `meow plan` | Write the sprint plan only, without implementing it. |
| `meow orchestrate` | Split an objective, Jira issues, or a task file into tasks, run them in parallel, merge them into one branch, review it, and open a pull request. |
| `meow status` | Inspect a saved or background run. |
| `meow cancel` | Ask a running sprint to stop cooperatively. |
| `meow worktree` | Inspect or safely clean MEOW-owned run worktrees. |
| `meow resume` | Inspect or continue a saved run. `--repair` re-baselines blocked resume gates (plan, config, branch, workflow, worktree, missing sessions). A partially finished task graph resumes and reruns only unfinished tasks. |
| `meow detach [--log FILE] -- CMD...` | Start a long command fully detached (own session, log file); prints JSON with `pid` and `log`. |
| `meow queue` | Enqueue tasks or run queued tasks serially in the current repository. |
| `meow workflows` | List, show, validate, or eject workflow definitions. |
| `meow agents` | List, show, eject, or diff agent definitions. |
| `meow ui` | Open the local catalog and manage command sessions for registered repositories. |
| `meow schedule` | Schedule an unattended run for later, list or edit schedules, or cancel a trigger. |
| `meow hooks` | Manage optional host hooks after reviewing what they install. |
| `meow ipython` | Open the interactive MEOW IPython session. |

`mw` is a shorter alias for the `meow` binary, and each command has a
single-letter shortcut: `r` run, `p` plan, `o` orchestrate, `rv` review, `l` lint (`run
--lint-fix`), `s` status, `x` cancel, `c` resume, `q` queue, `w` worktree,
`h` hooks, `i` ipython. For example, `mw r "Add CSV export" --name csv-export`.

Running `meow` without a command or `meow ipython` opens an IPython session. Inside it, the
top-level MEOW commands are available as bare commands, for example
`status` and `native verify`. `run` is not bare, to keep IPython's built-in `%run`; use
`meow run "Add CSV export" --name csv-export`.
The explicit `%meow ...` magic and regular `!meow ...` shell form are also
available.

### Scheduling a run

```bash
meow schedule add --at "2026-12-01T09:00:00+02:00" -- run "Add CSV export" --name csv-export
meow schedule add --at "09:00" --work-dir /path/to/project -- run --jira PROJ-123
meow schedule list --work-dir /path/to/project
meow schedule edit <schedule-id> --at "2026-12-02T10:30:00+02:00" --work-dir /path/to/project
meow schedule cancel <schedule-id> --work-dir /path/to/project
```

`--at` accepts an ISO date and time (with an optional timezone offset), or
`HH:MM` for the next occurrence in the machine's local timezone. Full timestamps
must be in the future. Each trigger runs once, through Windows Task Scheduler
or a Linux systemd user timer, even after the CLI or UI closes. Windows requires
the user to be logged in; Linux requires a working systemd user manager, with
lingering enabled separately for execution while logged out.

The command after `--` must be `run`. MEOW adds `--auto` and the selected
project directory, validates the run flags, and rejects manual approval,
`--no-worktree`, `--lint-fix`, `--background`, and `--from`. Requests and arguments
are saved as data under `.meow/schedules/`, with the run's output in the matching
`.log` file. The JSON response includes the identifier, UTC execution time, and
state. `edit` changes a pending schedule's time while keeping its identifier,
request, and frozen settings. Started, completed, and cancelled schedules cannot
be edited. If updating the OS trigger fails, MEOW attempts to restore the original
time and reports the failure. Cancellation removes the OS trigger; an already running job uses the
normal run or session stop controls. Keep MEOW's Python installation and project
at their registered paths. Credentials must be available to the scheduler user.

In plugin sessions, `/meow:schedule` resolves requests such as "run this tomorrow
at 9" and registers the same CLI schedule. `/meow:run` routes future-time requests
to that skill before beginning work. In the UI session launcher, select
**Schedule for later**, choose **Run at**, preview the command, and select
**Schedule session**. The reviewed recipe is frozen for that execution; session
details show the time, **Edit time**, and a cancellation control. **Edit time**
opens a local date/time input; **Save time** updates the pending trigger.
UI times use the browser's
local timezone and are stored as UTC.

### Selecting and editing workflows

`meow run --workflow NAME` selects `default`, `quick`, `review-only`, or a
custom workflow. Without the flag, `[workflow].default` chooses it. Repeat
`--input name=value` for string inputs available to templates and conditions
as `inputs.name`; the last repeated value wins. These options work with Jira
and background feature runs and are rejected with `--lint-fix`.

```bash
meow run "Audit authorization" --workflow review-only --input topic=API
meow workflows list
meow workflows show quick
meow workflows validate [NAME]
meow workflows eject quick --scope local
meow agents list
meow agents show reviewer
meow agents eject reviewer --scope project
meow agents diff reviewer
meow ui [--port 8765] [--no-browser]
```

`meow ui` serves the bundled app on 127.0.0.1 only. It shows workflows, agents,
and skills from every scope across the current repository and repositories
remembered in `~/.meow/repos.toml`; the repository picker narrows the catalog.
The `+ New` button adds a workflow, agent, or skill by copying an existing one
under a new name (or creates a blank skill). Definitions can be saved at project
scope (`.meow/`), local scope (`.meow/local/`, uncommitted), or user scope
(`~/.meow/`). Skills are stored under `.meow/plugin/skills/`,
`.meow/local/plugin/skills/`, or `~/.meow/plugin/skills/` for those scopes.
Saving the first skill at a scope also writes its plugin manifest and registers
it in Claude Code settings (`.claude/settings.json` for project scope,
`.claude/settings.local.json` for local scope, or `~/.claude/settings.json` for
user scope). Workflow details show the step sequence and expandable source, and
each workflow has ready-to-copy CLI and `/meow:run` commands. Search covers name,
description, and scope; Ctrl+K (Cmd+K on macOS) focuses search. The catalog
refreshes every two seconds while the page is visible.

The **Sessions** page launches MEOW commands directly through the CLI or through
Claude Code and keeps their output and attempts together. Select a registered
repository, then choose a command or start from a workflow, skill, or supported
Claude agent. The launcher previews the exact command and validates its flags;
CLI commands use argument arrays and run headlessly without interactive stdin.
The CLI command picker includes `orchestrate`: enter its objective in Request
and add options such as `--from`, `--max-parallel`, `--dry-run`, `--test`, and
`--no-pr` as argument tokens. These recipes start a new orchestrator; use
`meow orchestrate status`, `resume`, or `cancel` in a terminal for its lifecycle
controls. Orchestration always owns its worktrees and may push its integration
branch after final review, including when `--no-pr` is selected.
Feature runs always use unattended execution in an isolated worktree; the UI cannot answer approval prompts. Existing
verified unattended delivery policy still applies: a successful run can commit
and push its worktree branch. Running the UI locally does not turn that delivery
behavior off. Commands that do not accept `--auto` still run headlessly,
without receiving that unsupported flag.

Claude sessions use the installed Claude executable and a noninteractive
permission policy that denies operations requiring an unanswered prompt:
`--permission-mode dontAsk`, plus `--permission-prompts none` when the installed
Claude version supports it. Versions without the latter flag still use `dontAsk`.
The launcher reports when Claude is missing, unauthenticated, too old for the
required capabilities, or cannot resolve the selected MEOW plugin or agent.
Permission bypass is an explicit opt-in: enable the labeled bypass option to
send both `--allow-dangerously-skip-permissions` and
`--permission-mode bypassPermissions`. It is off by default.

Session recipes, attempt records, and separate output logs are saved in the
selected repository under `.meow/ui/sessions/`. Editing changes the recipe for
the next attempt; it does not rewrite a running attempt or saved output. Rerun
starts a fresh attempt (and a fresh worktree for feature runs); duplicate copies
the recipe into a new draft without starting it. Resume continues a stopped
resumable execution (`meow resume RUN_ID --continue` for CLI runs or
`claude --resume SESSION_ID` for Claude). A product-decision pause can receive a
clarified request, and resume repair remains an explicit choice. Completed runs
offer rerun instead. Archive hides an inactive session and can be reversed; it
does not delete output, transcripts, or worktrees. Stop preserves output and
resume identity.

The launcher's Advanced arguments panel derives the chosen command's flags,
aliases, descriptions, value types, and choices from MEOW's CLI parser. Select
flags using checkboxes and edit their values in the provided controls; Remove
clears the whole flag and its values. Flags managed elsewhere in the launcher
or incompatible with unattended feature runs remain visible with an explanation.
Review and orchestration recipes may leave Request empty when a source option
such as `--branch`, `--jira`, or `--tasks` supplies the work. The command preview
checks all combinations before launch.

The page also imports existing MEOW runs from registered repositories. Their
run facts and supported actions remain available; historical live output is
shown only when a background log exists. A rerun or duplicate from an imported
run opens an editable recipe from recorded facts and cannot recover flags that
were never recorded. The React/Vite frontend is bundled with MEOW, so Node.js is
not needed to run `meow ui`. Press Ctrl+C to stop the server. For frontend
development and build instructions, see [frontend/README.md](../frontend/README.md).

Catalog commands accept `--work-dir PATH`. Eject defaults to project scope,
also accepts local/user, and refuses to overwrite. `show` and workflow
`validate` warn when an ejected definition's shipped origin has changed.
`meow native workflow NAME` returns resolved JSON, including expanded steps
and the definition fingerprint. `meow native prompt ROLE` uses resolved agent
prompts/models and includes tools, skills, delegates, and session metadata.

Status shows the workflow name/scope and custom stage results. Workflow-aware
continuation reloads the saved name, checks its fingerprint, skips completed
top-level steps, and resumes an interrupted loop review-first. Journals created
before workflow metadata use the shipped `default` workflow regardless of the
current configured default. See [WORKFLOWS.md](WORKFLOWS.md) for the complete
schema, step behavior, templates, and resume rules. Named workflows in
`/meow:run` use CLI execution; native helpers do not drive custom step trees.

### Queueing work

Use `meow queue "request"` to persist work for the current repository. This
works while another MEOW run is active or while the repository is idle. Run
`meow queue` without a request to process pending items in FIFO order. A
repository-scoped worker lock ensures that two sessions cannot process the
same queue concurrently; a failed task pauses the queue for inspection and
retry.

## Optional editor hooks

`meow hooks status claude` reports `active`, `missing`, or `modified` for each
MEOW Claude Code hook by comparing the local installation manifest with Claude
settings. It only reads files. `meow hooks install claude --only lint` installs
an accepted subset; repeat `--only` for more hooks. Onboarding shows each hook's
host event, exact command, and effect before installation. Editor hooks are
optional: `meow run`, including `--auto`, works without them. Lint
failures and shaping advisories reach Claude as PostToolUse
`additionalContext`; plan advisories appear as a Stop `systemMessage`.

Use `meow hooks uninstall claude` to remove hooks recorded by MEOW's manifest.
The supported hook names are `lint`, `shaping`, `plan_capture`, and `plan_stop`.
Uninstall only removes the MEOW-owned entries; it does not rewrite unrelated
Claude settings.

`plan_capture` runs on PostToolUse after a `Write|Edit` of a plan file and
records a draft only when no sidecar exists. `plan_stop` runs on Stop and checks
the single open plan; it stays quiet when there are zero or several open plans.
Re-running `meow hooks install claude` migrates old Stop-based installs.
`meow hooks status claude` reports a hook installed on the wrong event as
`modified`.

## Quality concerns

Reviewers may record concrete maintenance concerns with a repository path,
exact code evidence, impact, and a suggested follow-up. MEOW deduplicates them
across review rounds and displays only live concerns observed in the current
run in `meow status`. `meow status --verbose` also shows their evidence and
follow-up. Concerns are advisory and do not independently fail a verification
gate. MEOW does not calculate a numerical project quality score.

## Browser checks

When `[tester.browser]` uses `kind = "command"`, MEOW starts the configured
`[[tester.dev_server]]`, waits for its readiness URL, runs the project's browser
test command, records named flows and changed artifact paths, then stops the
server. A required browser failure or unavailable provider blocks completion.
`meow status` shows separate lint, test, build, and browser results. Browser
artifacts should live under `.meow/` so verification output does not change the
code revision being checked.

## Project understanding

`meow run` and `meow plan` inspect project guidance and code before planning.
When a request needs design choices, they shape the requirements and may map
complex UI or cross-component work as a breadboard. Clear requests proceed
directly to planning. These phases run automatically and require no separate
command. An unresolved product choice stops with a checkpoint, including with
`--auto`; unattended runs never wait for input. Feature runs do not
suggest or edit prose documentation. Onboarding uses the same project audit
internally and can create selected knowledge documents as part of setup.

## Common options

Every public command accepts `--work-dir PATH` (also `-d`) to select a project
directory. Native helper commands accept the same path as `--working-dir` (and
also accept `--work-dir`/`-d`). By default, MEOW-generated plans and reviews
are written under `.meow/plans/`; an explicit `docs_dir` remains supported for
existing projects. Project documentation stays under `docs/`.

`run` and `plan` use a worktree by default. `--no-worktree` runs in the
selected directory instead; in that mode, the feature name is optional.

## Build and plan

```bash
meow run "Add CSV export" --name "add-csv-export"
meow plan "Add CSV export" --name "add-csv-export"
```

`run` plans, implements, and reviews the feature. `plan` writes only the plan.
Both accept `--from BRANCH` (also `-b`) to create a
worktree from a specific branch. A run that plans from scratch always gets its
own worktree: if `.worktrees/NAME` exists it uses `NAME-2`, `NAME-3`, and so
on, and plan, review, and test files take that name. Runs that continue
existing work (`--plan`, `--resume-at review`, `meow resume`) reuse the
existing worktree and take priority over a new source branch.

`run` (including `--jira` and `--lint-fix` without `--report-only`) refuses to
start while the checkout has uncommitted changes other than `.gitignore` and
`.meow/`, unless it creates a worktree from an explicit `--from` branch.
`plan` and `review` never require a clean tree.

On a project that was never onboarded, the first run onboards it automatically
before planning: it repairs the `.gitignore` boundary (`.meow/*`, `!.meow/`,
`!.meow/config.toml`) and writes a minimal `.meow/config.toml` with the lint
command it detects (ruff or eslint). Nothing is prompted and there is no
opt-out flag. Setup stays in the original project checkout as local changes,
so onboarding edits do not enter a feature or orchestrator task branch.
Python editable-install metadata (`*.egg-info/`) is ignored locally through
Git's `info/exclude`. `plan`, `review`, `run --lint-fix`, queued runs, and Jira runs
onboard the same way; onboarding never overwrites an existing config and an
onboarding failure never fails the command. `/meow:onboard` is for add-ons.

After required checks and review pass, a feature run in a separate linked
worktree commits and pushes its branch to `origin`. A detached feature worktree
receives a `meow/RUN_ID` branch at delivery. `--auto` opts into the same
non-interactive delivery path and is required for `--background`; it still
requires an isolated worktree. An in-place `--no-worktree` run does not
automatically commit or push. A missing remote or failed push leaves the
worktree and checkpoint for recovery. `--auto` supports `--from BRANCH`
(also `-b`), including with `--background`; the source branch supplies the
starting commit, and delivery pushes a separate `meow/RUN_ID` branch.
`--auto` cannot be combined with `--no-worktree`, manual plan approval,
resume-at-review, or lint-fix mode.

`run --manually-approve-plan` (also `-m`) displays the plan and waits for
approval before implementation. Declining exits without starting the
generator. `plan` does not accept this option because it never implements.

To continue an older plan without a saved run, use `--resume-at review`. MEOW skips planning
and reviews the current code first, using `--plan PATH` if supplied or
the latest plan in `docs_dir` otherwise. If that review finds work, the
generator continues from there. The default, `--resume-at generate`, starts
with a fresh plan unless `--plan` is supplied.

### Run status and recovery

Each `meow run` saves an atomic checkpoint under `.meow/runs/`. The directory
is ignored by Git. Inspect the latest run or a specific ID without starting
agents:

```bash
meow status
meow status RUN_ID --verbose
meow resume RUN_ID
meow resume RUN_ID --continue
```

`resume` inspects and exits by default. `--continue` validates the saved
repository, worktree, branch, plan, and configuration before starting agents.
If the phase is `needs_user_decision`, status shows the original request,
the wording that triggered the stop, and a command to supply the intended
outcome in the same run:

```bash
meow resume RUN_ID --continue --request "Retain accounts and their history forever"
```

`--request TEXT` replaces the full request only for a product-decision pause
before planning. Include the original requirements that still apply. MEOW keeps
the original request in the journal and reuses the saved worktree. Empty or
still-unresolved requests are rejected without changing the checkpoint;
resuming such a pause without `--request` prints the clarification command.
Inspection alone cannot change a request, and planned or already-started work
cannot replace its request through this flag.

A run interrupted during a possible edit reviews the saved worktree against
its plan before any generator retry. A mismatch stops with a recovery
diagnostic. Failed and interrupted runs retain their worktree and evidence.
Completion requires an independent reviewer PASS, an enabled tester PASS, and
current passing required lint, test, and build checks. Advisory failures remain
visible. Status reports SDK turns, tokens, and USD cost accumulated from received agent results. The verbose view lists each result by role. Missing SDK metrics are shown as `unavailable`.

Claude owns the conversation transcript and its retention. MEOW keeps only the
small role-to-session-ID references needed to resume the generator or reviewer
in the atomic run JSON; it does not duplicate Claude's transcript or event
store.

`meow cancel RUN_ID` requests cancellation of an active run. The runner checks
the request between phases and while awaiting agents or verification commands.
It stops those tasks, keeps the worktree and checkpoint, and records a
`cancelled` phase. Resume explicitly with `meow resume RUN_ID --continue`;
resumption validates the checkout and reviews existing edits first when a
generator had started. Cancellation does not signal an unrelated process ID.

Use `meow run "REQUEST" --name NAME --auto --background` to detach a
local feature run from its launching terminal. The command returns a run ID
immediately. `meow status RUN_ID` shows the worker state and its run-owned log
path; `meow cancel RUN_ID` requests a cooperative stop. If a worker disappears,
status marks the run interrupted and shows the resume command. Background
execution requires `--auto` and cannot wait for plan approval.

An optional `[background]` table can set `notify_command` to a literal
argument array, for example `notify_command = ["notify-meow"]`. MEOW appends
only the run ID, terminal phase, and status command. Notification failure is
recorded separately and cannot change verification results.

Inspect saved run worktrees with `meow worktree list` and
`meow worktree inspect RUN_ID`. `meow worktree clean RUN_ID` removes only a
completed, registered MEOW worktree inside the repository's `.worktrees`
directory when it is clean and its branch has no unpushed commits. A detached
branch or unknown push state blocks cleanup. There is no force cleanup flag.

Onboarding can configure `[worktree_setup]` with selected regular files and
literal command argument arrays. Setup runs once for a newly created feature
worktree before planning; each completed action is recorded in the run
checkpoint. Paths containing `..`, links, and secret-like file names are
rejected. Failed setup retains the worktree for inspection and retries on the
next preparation. Native `prepare`, SDK runs, plan-only runs, branch reviews,
and task worktrees apply setup before agents run. Missing Node dependencies are
installed with the project's package manager, including on reused worktrees and
in-place runs; installation commands obey the `setup` permission role. See
[worktree setup and Bun shared storage](INTEGRATIONS.md#new-worktree-setup).
Each configured command is approved as an exact argument array during
onboarding. A `[permissions]` rule for the `setup` role and `WorktreeSetup`
tool can further deny it or require approval; unattended runs stop in either
case.

### Jira issue run

```bash
meow run --jira PROJ-123
meow run --jira
```

This fetches the selected issue (or the latest issue in the configured Jira
project), runs the sprint in its own worktree, commits and pushes the passing
branch, and prints a JSON result on success. Configure `[jira]` and
`[jira.mcp]` as described in [INTEGRATIONS.md](INTEGRATIONS.md).

The Jira mode always creates its own worktree and branch, so it rejects
`--name`, `--no-worktree`, `--from`, `--resume-at`, and `--plan`.
It accepts `--manually-approve-plan` for interactive runs; do not use that
option for unattended scheduled runs.

### Lint fix

```bash
meow run --lint-fix
meow run --lint-fix --report-only
```

The default mode runs configured auto-fix commands and sends remaining
findings to a fixer agent until clean or `max_rounds` is reached. It edits the
selected directory in place and requires a clean working tree.

`--report-only` prints lint output without editing files or starting an agent.
It is intended for the `/meow:lint` skill, which performs fixes in the
calling session.

## Orchestrate

`meow orchestrate` turns one objective into one reviewed branch and one pull
request. It splits the work into a graph of tasks, runs every task as its own
unattended `meow run` sprint in a detached worker and worktree (up to
`--max-parallel` at a time), merges each passing task into the integration
branch `orchestrator/ID`, recovers failed tasks through triage, reviews the
combined branch against the objective, pushes it, and opens a pull or merge
request. When a task cannot be recovered or the final review keeps failing, it
stops with nothing pushed and a report that names the blocker.

```bash
meow orchestrate "Add SSO login and an admin audit log"
meow orchestrate --jira PROJ-1 PROJ-2
meow orchestrate --jql "project = PROJ AND sprint in openSprints()"
meow orchestrate --tasks tasks.toml
meow orchestrate "Add SSO login" --dry-run
meow orchestrate status [ID]
meow orchestrate resume ID [--task T --request "New request text"]
meow orchestrate cancel ID
```

Give exactly one input: the objective text, `--jira KEY [KEY ...]`,
`--jql QUERY`, or `--tasks FILE`. A first word of `status`, `resume`, or
`cancel` selects that action instead, so quote an objective that begins with one
of them. Like `meow run`, a start refuses a checkout with uncommitted changes
(other than `.gitignore` and `.meow/`).

| Option | Meaning |
| --- | --- |
| `--jira KEY [KEY ...]` | One task per Jira issue, with the request text `Resolve Jira issue KEY: SUMMARY` followed by the description. Read-only: nothing is written back to Jira. Needs `[jira]` and `[jira.mcp]`. |
| `--jql QUERY` | One task per issue the query matches (same rules as `--jira`). |
| `--tasks FILE` | Run the tasks in a TOML or JSON file (below); no decomposition. |
| `--from BRANCH` | Base branch for the integration branch. The default is the current branch; a detached `HEAD` needs `--from`. |
| `--max-parallel N` | Tasks running at once. The default is `[orchestrator].max_parallel` (3). |
| `--dry-run` | Decompose, print the task graph, and stop before anything runs. `meow orchestrate resume ID` continues it. |
| `--test` | Run the configured tests and exploratory testing in every task. |
| `--no-pr` | Push the integration branch but do not open a pull request. |
| `--task T`, `--request TEXT` | `resume` only: reset the blocked task `T` (default: the task the blocker names) and optionally replace its request text. |

An objective or a Jira set is decomposed by a read-only agent into at most 32
tasks with `depends_on` edges and the paths each is expected to touch. Jira
issues each stay their own task: the agent may only add dependencies and
paths. The graph is saved as `.meow/orchestrators/ID/tasks.json`. Invalid
output gets one corrective retry, then the orchestrator fails. More than 32
tasks fails immediately: narrow the objective or the JQL query.

A tasks file holds the same entries, as TOML:

```toml
[[tasks]]
id = "schema"
request = "Add the audit_log table and its migration"

[[tasks]]
id = "endpoint"
request = "Add GET /audit that reads the new table"
depends_on = ["schema"]
expected_paths = ["src/api/audit.py"]
```

or as JSON (`{"tasks": [{"id": "schema", "request": "..."}]}`). `id` is 1-64
letters, digits, `-`, or `_`, starting with a letter or digit, and unique
ignoring case. `depends_on` lists other task ids. `expected_paths` are optional
project-relative hints; they may overlap.

A task starts when every dependency is merged, most-depended-on first. Its
branch is `orchestrator/ID-TASK` in `.worktrees/orch-ID-TASK`, created from the
integration branch tip when the attempt starts (`orchestrator/ID` lives in
`.worktrees/orch-ID`). A passing task is committed on its branch and merged with
`--no-ff` at once. A conflict goes to an integrator agent that may edit only the
conflicted files, the configured lint and checks run after every merge, and a
merge that fails them is rolled back and sent to triage. A failed attempt goes to
a triage agent that chooses `retry`, `resume`, `rescope`, `reintegrate`, or
`blocked`; every task gets at most `[orchestrator].max_recovery_cycles` (3)
recovery cycles, and a lineage split from a task shares its counter.

When every task is merged, a reviewer checks `base...orchestrator/ID` against
the objective, with fix rounds up to `[orchestrator].review_rounds` (3). Then the
branch is pushed and a pull request (GitHub) or merge request (GitLab) is
opened, titled `Orchestrator: FIRST LINE OF THE OBJECTIVE` (Jira sources list
the keys) with the report as its body. Nothing is pushed before this point. If
no provider is configured or the request fails, the pushed branch stays, the
orchestrator still ends `delivered`, and the command returns exit code 3 and
prints `PR not opened: REASON` with a manual link on stderr.

| Exit code | Meaning |
| --- | --- |
| 0 | Delivered with a pull request (or `--no-pr`). |
| 3 | Delivered without a pull request. |
| 1 | Failed (configuration, git, or an unrecoverable error). |
| 2 | Blocked: nothing was pushed; the report names the blocker. |
| 130 | Cancelled. |

On delivery it prints one JSON line,
`{"orchestrator": "ID", "branch": "orchestrator/ID", "pr": "URL or null", "tasks": N}`.
Blocked and failed runs print the report, blocker first, with the exact resume
commands. No dollar budget is enforced; usage is recorded per child run and
summarized in the report.

State lives in `.meow/orchestrators/ID/`: `state.json` (written atomically after
every transition), `tasks.json`, `report.md`, a `cancel` marker, and
`driver.lock`. `meow orchestrate status [ID]` prints the phase, each task's
status, cycles, branch and run, the blocker, and the pull request. `meow status`
adds one roll-up line (`orchestrator ID PHASE merged/total merged`) per active
orchestrator and lists each task's sprint as a run with source `orchestrator`
(continue one by hand with `meow resume`).

`meow orchestrate resume ID` continues after a crash, a block, a failed push, or
`--dry-run`. Merged tasks never rerun, live children are re-attached, a
half-finished merge is rolled back to its recorded commit, and a delivered
orchestrator whose pull request failed retries only the pull request. A blocked
orchestrator needs its blocker resolved first; `--task`/`--request` reset a
blocked task and may replace its request. `meow orchestrate cancel ID` cancels
the live children and records `cancelled`; nothing is pushed. One driver owns an
orchestrator at a time, either a terminal (`meow orchestrate`) or a Claude Code
session (`/meow:orchestrate`); a second driver is refused while the first is
alive, and a session that has not heartbeat for 10 minutes can be taken over.
Either can resume the other's orchestrator.

Worktrees and task branches are kept until you clean them.
`meow worktree clean ID` (the 12-character orchestrator ID, not a run ID)
removes the task and integration worktrees of a delivered orchestrator when
every worktree is clean, every task branch is merged into the integration
branch, and `origin` holds the delivered commit. Branches are never deleted.

## Review

```bash
meow review
meow review "Check API error handling"
meow review --jira PROJ-123
meow review --gitlab "https://gitlab.example.com/group/project/-/merge_requests/123"
meow review --github "https://github.com/group/project/pull/123"
meow review --ci
meow review --branch feature/add-csv-export --target main
meow review --plan .meow/plans/add-csv-export.md
```

With no source, review uses the latest plan or falls back to a code-diff
review. Other sources are a free-text prompt, Jira issue, GitLab merge request,
GitHub pull request, local branch diff, or specific plan file. Only one source
may be selected.

`--ci` reviews the exact detached GitLab pipeline checkout against the fetched
target ref. It is report-only and cannot be combined with another source,
`--fix`, `--test`, or `--no-worktree`; see [GitLab CI review](#gitlab-ci-review)
for the pipeline contract.

Reviews are report-only by default: one reviewer pass writes a PASS/FAIL
verdict without editing. Add `--fix` to loop through review, fixes, and
another review up to `max_rounds`. This option is supported for all sources
except `--gitlab` and `--github`, which have no local checkout to edit.

Branch reviews require `--target TARGET` and use an isolated worktree by
default. Add `--no-worktree` to fix in place, but the selected directory must
already be on the reviewed branch. Other review sources use the selected
working directory directly and do not require a clean tree.

`--review-file PATH` (also `-r`) resumes fixing an existing prompt- or
plan-based review. Pass the original prompt when resuming a prompt-based
review. GitLab, GitHub, and branch reviews cannot be resumed this way; rerun the
original source instead. Review files are saved under the configured plan
directory, which defaults to `.meow/plans/`, with names based on their source:
`<plan>-review.md` for plan reviews, and `prompt.<id>.review.md`,
`gitlab.<id>.review.md`, `github.<id>.review.md` or `branch.<id>.review.md`
(a random 8-hex `<id>`, so concurrent reviews never overwrite each other).
Older fixed names such as `review.md` still resume.

Configure `[jira]`/`[jira.mcp]` for `--jira`, `[gitlab.mcp]` for `--gitlab`, or
`[github.mcp]` for `--github`.
See [INTEGRATIONS.md](INTEGRATIONS.md).

## Native and CLI skill modes

The workflow skills `/meow:plan`, `/meow:review`, `/meow:lint`, and
`/meow:orchestrate` use native mode by default: the calling session plans,
fixes, or supervises, while a fresh subagent reviews each round where
applicable. (`/meow:orchestrate` still runs every task's sprint as a detached
CLI worker; only its decomposing, triage, conflict, and review roles are
native.) `/meow:run` uses CLI mode by default;
native run is an explicit opt-in. Jira, GitLab, and GitHub access in native
mode comes from MCP tools connected to that session. Ask for "CLI mode" (or
headless mode) when a native-default skill should run the `meow` CLI instead.
`/meow:onboard`, `/meow:migration`, and `/meow:customize` are guidance flows
that configure or explain the system rather than alternate execution engines.

Both modes read the same active MEOW configuration, use the same worktree rules
and `max_rounds`, and write the same plans, reviews, and verdict format. The
shareable project configuration is `.meow/config.toml`. Configuration priority
is `.meow/config.local.toml`, project `.meow/config.toml`, user
`~/.meow/config.toml`, then built-in defaults. `agent_skills.default` and
`agent_skills.<role>` are the exception: their lists append across all config
layers. The CLI is the option for
unattended terminal or scheduled runs. See the shared
[native mode protocol](../skills/_shared/native-mode.md).

Custom skills and agents from `[custom]` apply in both modes: the CLI loads
them into each role's SDK session, and native skills read them with
`meow native custom --role ROLE`. See
[INTEGRATIONS.md](INTEGRATIONS.md#custom-skills-and-agents).

## Maintainer and internal commands

These commands are hidden from `meow --help` because they are not the normal
user surface.

`meow _scheduled ID --work-dir PATH` is the OS scheduler's worker entry point.
It atomically claims the saved schedule, runs it at most once, and records its
result. Use `meow schedule` to create, inspect, or cancel schedules.

`meow docs-update` is for maintainers updating MEOW's own prose docs on a
clean checkout of `[delivery].target_branch` (default `dev`). Run
`meow docs-update --since REF` the first time, using
the commit from which documentation should be reviewed. Later, run
`meow docs-update` after the previous documentation changes and
`docs/.meow-docs-update.json` have been committed. It compares the saved
inspected commit with the current HEAD, updates relevant prose, and prints its
baseline, edited paths, and diff. Review the documentation and marker together,
then commit them yourself. It does not commit or push.

`meow native` is the JSON helper used by MEOW's in-session skills. Skill
authors and maintainers may call it while debugging a skill flow, but normal
feature work should use the `/meow:*` skills or the public CLI commands above.

`meow evaluate` inspects saved run quality for harness maintainers. It is for
comparing or auditing MEOW runs, not for building, reviewing, or resuming
project work.

`meow _worker` is a private implementation detail used by
`meow run --auto --background` to start the detached local worker. Humans
should not invoke it directly; use `meow status`, `meow cancel`, and
`meow resume` to interact with background runs.

`meow _orchestrator-task` is the same kind of private detail: `meow orchestrate`
uses it to run one task's sprint in a detached worker. To follow those tasks,
use `meow orchestrate status` and `meow status`.

## GitLab CI review

`meow review --ci` reviews the exact GitLab pipeline checkout against the
fetched `refs/remotes/origin/<target_branch>` commit (`[delivery].target_branch`,
default `dev`). It accepts a detached source checkout
when `HEAD` matches `CI_COMMIT_SHA`. The review is report-only: it does not
plan, fix, commit, push, or open a merge request. It uses the local Git history,
so GitLab MCP is unnecessary.

Include [the GitLab review job](../templates/gitlab-ci-review.yml) in a project's
pipeline for detached merge request pipelines targeting the target branch
and push pipelines on other branches; a merge request aimed at any other branch
is rejected. This repository ships the template; it does not require
a root `.gitlab-ci.yml`. In merge request pipelines, the reviewer reads the MR
description as the review brief; GitLab descriptions longer than 2,700
characters are truncated and flagged. Push pipelines have no MR brief and
review the diff alone. The job fetches the target branch without moving HEAD,
uses full Git history for a reliable merge base, and uploads
`.meow/ci-artifacts/review.md` and `.meow/ci-artifacts/verdict.json` even when
the job fails. Provide the Claude Agent SDK credentials through masked CI
variables. The job must install this project and fetch the target ref before
running the command. Merged-result, tag, and other pipeline types are rejected.

The command also accepts `--target-ref REF` for controlled use,
`--artifact-dir PATH`, and an existing `--plan PATH`. PASS exits 0, FAIL
exits 1, and an unverified or infrastructure failure exits 2. The artifacts
record the source and target SHAs, merge base, verdict, and failure reason.
No local branch checkout or worktree is required.

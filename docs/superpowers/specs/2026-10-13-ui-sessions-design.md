# MEOW UI command execution and sessions

Date: 2026-10-13
Status: design approved in chat; written spec awaiting review

## Purpose

Extend the existing local React/Vite/shadcn catalog into a place to launch MEOW
commands and manage their executions. Users can select direct CLI execution or
Claude Code, see output as it arrives, edit launch settings, resume, rerun, and
duplicate sessions. Both modes default to unattended operation. Nothing is
hosted or deployed.

## Interface

Add Sessions beside Workflows, Agents, and Skills in the existing navigation.
Keep the existing system sans typography, lavender accents, repository selector,
and distinguishing parent paths for repositories with identical names.

The Sessions view has a searchable session list and a selected-session panel.
The panel leads with status and actions, followed by live output. Launch settings
and linked run facts are available alongside output on desktop and below it on
small screens. Status is expressed with text as well as color. Output uses the
existing bundled monospace font, wraps optionally, has copy/download actions,
and follows new output only while the user is at the bottom. Keyboard focus,
loading, disconnected, empty, and failed-launch states are explicit.

New session opens a structured launcher: repository, execution mode, command,
request, optional name, workflow, inputs, and command-specific options. Show the
exact generated command before execution. An advanced argument list supports
additional public MEOW command flags without invoking a shell. Reject invalid
combinations before launch; preserve entered values on errors. CLI is the initial
execution mode. Remember mode within a draft without silently enabling bypass.

Workflow Run preselects `meow run --workflow <qualified-name>`. Skill Run selects
Claude and the qualified slash command. Agents are roles rather than CLI
commands: their Run action selects Claude's agent option with a request, and
only appears when that definition can be resolved by Claude. Never invent a
`meow agents run` command. Builtin MEOW skills map to their existing slash
commands; unsupported CLI/Claude combinations explain which mode supports them.

## Session and attempt model

A UI session is a saved launch recipe plus an ordered history of executions.
Each attempt stores its own immutable effective settings, timestamps, process
identity, state, exit code, output location, Claude session ID when applicable,
and linked MEOW run IDs. Store these under the selected repository's
`.meow/ui/sessions/` with atomic JSON writes and separate append-only output
files. UI session IDs and attempt IDs are validated UUID-derived identifiers.
MEOW's existing `.meow/runs/` journals remain authoritative for workflow phases,
worktrees, role-to-Claude-session IDs, checks, and delivery.

Actions have distinct semantics:

- Edit changes the session name and recipe for the next attempt, including
  request, arguments, execution mode, and options. It does not rewrite a
  running process, old output, Claude transcripts, or completed run journals.
- Rerun launches a fresh attempt from the current recipe. Feature runs get the
  usual fresh MEOW worktree/run; Claude gets a fresh conversation.
- Duplicate creates a new draft session with the recipe copied, no process,
  output, or Claude conversation identity. It does not start automatically.
- Resume continues a stopped resumable execution: use existing
  `meow resume <id> --continue` for CLI runs and `claude --resume <id>` for
  Claude conversations. Complete/non-resumable runs offer Rerun instead.
  Product-decision pauses accept a clarified request using MEOW's existing
  `--request` semantics. Resume repair remains an explicit option.
- Stop requests MEOW cooperative cancellation when a run ID exists, then
  terminates the owned attempt's process tree after a grace period. Claude
  attempts use the same owned-process termination without touching unrelated
  Claude processes. Preserve output and resume identity.
- Archive hides an inactive session by default and is reversible. Running
  sessions must be stopped first. No worktree or transcript deletion.

Concurrent attempts in different sessions are permitted. A session can have
only one active attempt; launch/resume claims are atomic to prevent double
submission. State includes draft, starting, running, stopping, succeeded,
failed, stopped, interrupted, and needs decision. Process completion and linked
MEOW phase are displayed separately: a zero exit code does not imply a paused
MEOW run has completed.

## Execution and unattended behavior

Direct CLI uses the server's Python interpreter and MEOW entry point, selected
repository as working directory, unbuffered output, merged stdout/stderr, and
no interactive stdin. Use argv arrays with no shell interpolation. For feature
run commands that support it, default to `--unattended` and isolated worktrees.
Preserve MEOW's delivery policy: verified unattended runs may commit and push
their worktree branch. Show that behavior in the launcher. Do not confuse local
UI hosting with suppressing the command's existing delivery behavior.

Commands that do not accept `--unattended` are still launched headlessly.
Do not pass unsupported flags. Incompatible options require an explicit recipe
change and validation; do not silently disable unattended operation. Resume
must propagate the saved unattended policy into `run_sprint` rather than lose
it through the current resume default. This targeted CLI fix needs regression
coverage and preserves ordinary CLI behavior.

Claude uses its installed local executable with `--print`, verbose streaming
JSON output, and partial messages. Send a prompt that invokes the selected
MEOW skill/command with the request and explicitly directs unattended operation,
without waiting for human input. Decode text, tool activity, results, and
permission denials into readable output, retaining the original events locally.
Default to a supported noninteractive permission policy that denies operations
requiring an unanswered prompt; do not bypass permission checks by default.
Inspect installed Claude capabilities and return a useful error for unsupported
versions, missing executable, authentication, or missing plugin/agent.

The Claude-only bypass switch is off by default and visibly labeled. When
enabled, pass `--allow-dangerously-skip-permissions` together with
`--permission-mode bypassPermissions`. The allow flag alone does not activate
bypass. Persist the selected policy per recipe/attempt and show it in details.
No actual Claude execution or charge is required by automated tests.

## Backend boundaries and persistence

Keep HTTP validation/routes in `ui/`. Put UI recipe storage and command
translation in focused UI-owned modules; put process launch, output capture,
identity checks, and stop/reconciliation in `infrastructure/`. Reuse existing
background/process identity and cancellation helpers where applicable. Avoid
adding a second workflow orchestration engine.

A detached local attempt worker owns the subprocess and writes state/output,
so closing a browser or restarting the UI does not kill executions. On restart,
reconcile saved attempts using worker identity, not PID alone. Missing workers
become interrupted with preserved output; do not automatically rerun work.
Persist failed launches as failed attempts. Workers use hidden windows on
Windows and owned process groups on POSIX. Stop validates ownership before
signaling; never terminate a reused PID. Place a per-session interprocess claim
around launch and state updates so multiple UI instances cannot run it twice.

Add JSON endpoints for list/detail, create/update, launch/resume/stop/duplicate,
archive/unarchive, and incremental output. Use bounded output reads with a
cursor and poll while selected/running; existing polling patterns suffice
without WebSockets. Cap response size, detect rotated/truncated logs, and make
polling cancelable. Download output is scoped to the validated session ID.

List saved MEOW runs across the registered repositories alongside UI sessions.
Imported runs have run facts and existing background logs when available;
show that historical live output was not recorded when absent. Offer supported
resume/cancel actions. Rerun/duplicate opens an editable recipe from recorded
request/workflow/config facts and does not pretend unrecorded original flags
are known. Link runs to UI attempts using an explicit persisted association
when created; avoid identifying runs by newest timestamp alone.

Retain loopback binding, known Host validation, same-origin mutation checks,
JSON body limits, repository allowlisting, and server-owned file paths. API
callers cannot supply executables, arbitrary log paths, shell scripts, or
process IDs. Validate command arguments with the actual MEOW parser and restrict
the launcher to public headless commands; exclude UI, IPython, internal workers,
and detach. Render output as text, never HTML. Runtime state is already ignored
by the repository's `.meow/*` rules.

## Validation and delivery

Backend tests use disposable repositories and deterministic local child
processes: command construction, unattended propagation, permission flags,
output cursors, persistence/restart, launch failures, duplicate launch races,
stop ownership, resume capability, imported logs, and Host/Origin/path defenses.
Frontend tests cover launching both modes, bypass default/selection, live
updates, scrolling, errors/retry, edit/rerun/duplicate/resume, stop/archive, and
repository filtering with duplicate names. Do not execute a billable Claude
run as part of the test suite.

Run the full pytest suite and Ruff gates, frontend typecheck, ESLint, formatting,
interaction tests, and production build. Rebuild the committed local UI bundle
and visually check desktop/mobile with deterministic session fixtures. Existing
CI already runs these frontend stages after MEOW tests; extend their coverage
without adding deployment. Update CLI/UI documentation with launch modes,
permission policy, persistence, editing semantics, and unattended delivery.

Implementation should occur in a separate feature branch/worktree. Merge and
push are separate delivery actions after implementation and verification.

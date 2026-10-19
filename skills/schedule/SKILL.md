---
name: schedule
description: Use when scheduling a MEOW request, workflow, or Jira issue for later on Windows/Linux, or listing, rescheduling, or cancelling its OS trigger.
---

# schedule

When the user asks MEOW to run at a future time, register the schedule now;
do not run the work immediately, sleep in the session, or use host-specific
cron/Task Scheduler commands yourself. Both CLI and native sessions use the
same deterministic helper. The future work runs through the automatic CLI,
outside the current conversation.

## Native mode

Use the CLI mode steps below from the current session too. Scheduling itself
starts no agents; the registered trigger starts the automatic CLI later.
Other native workflows follow [the shared protocol](../_shared/native-mode.md);
scheduling does not require loading it.

## CLI mode

1. Resolve the requested work, project directory, feature name, and time.
   Use the user's timezone when supplied; translate natural language like
   "tomorrow at 9" to an ISO date/time. A bare `HH:MM` means the next occurrence
   in the machine's local timezone. An ISO timestamp with an explicit offset
   selects an unambiguous instant. Never silently substitute your own timezone
   for one the user specified. Derive a safe feature slug if none was supplied.
2. Run (quote each argument safely for the current shell):

   ```text
   meow schedule add --at "<ISO timestamp or HH:MM>" --work-dir "<project>" -- run "<request>" --name "<feature>"
   ```

   For a Jira issue, replace the request/name with `run --jira "<KEY>"`.
   Preserve requested `--workflow`, `--input`, `--plan`, and `--test` flags.
   Scheduling adds `--auto`; it requires an isolated worktree and rejects
   manual approval, lint-fix, `--background`, and `--from`. It follows the
   project's normal verification and delivery policy when it runs.
3. Check the exit status. Only report a schedule as created when the command
   succeeds and returns JSON with `state: "scheduled"`. Report its `id`, the
   returned `at` instant in the user's timezone, and the project. If registration
   fails, report the error; do not claim a timer exists or silently fall back to
   an in-session delay.

Inspect schedules with `meow schedule list --work-dir "<project>"`. Change a
pending schedule's time with `meow schedule edit <id> --at "<time>" --work-dir
"<project>"`; resolve the time using the same timezone rules as creation. This
preserves the schedule identifier and frozen request/settings. Use `list` to
identify the existing schedule rather than creating a duplicate. Started,
completed, and cancelled schedules cannot be edited. Check the command's exit
status and returned time before reporting success. Cancel a
trigger with `meow schedule cancel <id> --work-dir "<project>"`. Cancellation
does not stop a run that has already started; use `meow cancel <run-id>` or the
UI session stop controls. CLI output and execution results live under
`<project>/.meow/schedules/`; regular run journals remain under `.meow/runs/`.

Windows uses Task Scheduler under the current user, without administrator
privileges or stored passwords. The user must be logged in. Linux requires a
working systemd user manager and `systemctl`; enable lingering separately if
runs should start while logged out. The machine must be available; missed
times are eligible to run when the scheduler becomes available. MEOW and its
Python environment must remain installed at their registered paths. Credentials
must be available to the scheduler user (shell-only environment secrets are
not copied into schedule files).

The UI offers the same operation: New session → Start → Schedule for later →
Run at → Preview command → Schedule session. UI schedules freeze the reviewed
recipe, show the time in session details, and can be edited with **Edit time**
or cancelled there. Editing the schedule time preserves its frozen recipe. Each
scheduled trigger is one-shot; create separate schedules for separate dates.

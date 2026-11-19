---
name: autopilot
description: Use when the user wants a MEOW build to keep going until it succeeds without them (autopilot, "don't stop", "finish it for me", "keep retrying"), to repeat any prompt or command until a check passes, or to check on or stop an autopilot.
hooks:
  Stop:
    - hooks:
        - type: command
          command: 'meow autopilot hook Stop --work-dir "${CLAUDE_PROJECT_DIR:-.}" || true'
  StopFailure:
    - hooks:
        - type: command
          command: 'meow autopilot hook StopFailure --work-dir "${CLAUDE_PROJECT_DIR:-.}" || true'
  Notification:
    - hooks:
        - type: command
          command: 'meow autopilot hook Notification --work-dir "${CLAUDE_PROJECT_DIR:-.}" || true'
  PreToolUse:
    - matcher: "Bash|mcp__.*"
      hooks:
        - type: command
          command: 'meow native merge-guard --work-dir "${CLAUDE_PROJECT_DIR:-.}"'
---

# autopilot

Autopilot runs the native `/meow:run` workflow and keeps it going until it
succeeds: success (`complete` or `delivered`) is the only end. It survives API
errors, crashes, closed windows, stalls and unanswered questions by relaunching
Claude on the run's conversation with the same flags. There is no attempt limit
unless the user names one ("at most 5 tries": pass `--cap 5` or keep `[run]
autopilot_cap`). Dependency or install problems: [dependencies](../_shared/dependencies.md).

## Native mode

The default: this session builds the request and autopilot guards it.

1. Follow [the native run procedure](../run/references/native.md) with
   [the shared protocol](../_shared/native-mode.md), with these changes.
2. Pass `--autopilot` on the **first** checkpoint (`meow native checkpoint
   preparing --intent run --autopilot ...`). Its JSON names the autopilot
   `loop`; tell the user it is on, and how to stop it (below).
3. Never ask the user anything, including plan approval, even when the procedure
   says to ask. Make the reasonable choice, note it in the plan or report, and
   continue. Treat ambiguity as yours to settle.
4. Round limits and stall rules do not stop you: `meow native round` never
   reports `exhausted` under autopilot. Fix review, check, gate and delivery
   failures and repeat until `meow native finalize` returns `complete: true`.
5. Do not `meow cancel` the run to start over. Under autopilot a cancel only
   restarts the current session; redo work inside the same run (reset the round
   counter, replan with a `planning` checkpoint) and keep its ID.
6. A run that needs the CLI tester is continued by autopilot through
   `meow resume --continue --repair`; do not route it yourself.

If this prompt says to **continue** a run ID, skip preparation: inspect
`meow status ID --json`, keep the given `--run-id`, `--work-dir` and
`--active-dir`, and continue from the last checkpoint (review an interrupted
edit against the plan first).

## CLI mode

From a terminal, or when the user wants it fully in the background, start the
same build detached: `meow autopilot "REQUEST" --work-dir "<project>" [--
CLAUDE_FLAGS]`. Continue a saved run under autopilot with
`meow autopilot resume RUN_ID`. To repeat any prompt or command until a check passes, run (detached; it returns
at once):

```bash
meow autopilot loop "Make the test suite pass" --until "pytest -q" --work-dir "<project>"
meow autopilot loop --command "npm run e2e" --until "test -f e2e-ok" --work-dir "<project>"
```

`--until CMD`: success is CMD exiting 0; without it, the command exiting 0.
`--command` replaces the default Claude launch (`[run] autopilot_command`).
Claude flags go after `--`.

## Watching and stopping

- `meow autopilot status [ID]`: state, launches, flags, log path (`ID` may be
  the run ID or the loop ID). `meow status RUN_ID --json` shows `supervised: true`.
- Stop only when the user explicitly asks to stop or turn off autopilot:
  `meow autopilot stop ID`. Never on your own initiative. Work is kept and the
  run stays resumable (`meow autopilot resume RUN_ID`).

## What autopilot watches

While it watches this session it kills and replaces it when Claude Code's
process exits, the session ends on an API error, a question or permission
prompt goes unanswered for `[run] autopilot_answer_wait` (600 s), or nothing
happens for `[run] autopilot_stall` (1200 s) with no tool running. A running
tool call (tests, builds, subagents) is never killed. Relaunches copy this
session's Claude flags (`--dangerously-skip-permissions`, model, MCP config,
...) plus `[run] claude_args`.

Unrelated failures still follow the shared contract: helpers exiting 75
(`MEOW_RETRY`) print a `next_command`; `MEOW_BLOCKED` means report and let
autopilot retry. See [runs](../../docs/RUNS.md#autopilot).

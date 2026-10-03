# Future features

## Run events and hooks

**Status:** Deferred. Do not implement as part of the current feature work.

MEOW could publish a small, versioned set of events at meaningful run boundaries: run started, plan accepted, phase started or completed, verification failed, run paused, and delivery completed. Each event would identify the run, phase, time, and path to supporting evidence. Checkpoints and `meow status` should reflect the same transitions.

Projects could configure optional handlers for uses such as CI status or notifications. Reporting handlers should not change a verified run's result when they fail. A handler explicitly configured as a required gate may block completion, and its failure should be recorded. Handlers need timeouts and declared permissions. In `--unattended` mode, they must never prompt for input; failures must leave a clear checkpoint and recovery path.

Add event types only when a concrete consumer needs them. Avoid exposing every internal agent message or tool action as a public event.

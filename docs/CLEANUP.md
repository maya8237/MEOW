# Run cleanup

Successful runs retain their result worktree and a readable `meow/<name>` branch.
`--name` supplies the name; otherwise MEOW derives it from the request. Existing
issue and orchestrator branches keep their required names. Collisions receive
numeric suffixes without overwriting earlier results.

Integrated task worktrees and owned, merged orchestrator task branches are removed
automatically after successful completion in CLI and native mode.

```bash
meow cleanup
meow cleanup RUN_ID --work-dir /project
```

With no ID, cleanup examines saved runs, including older runs. It retains each
run's result; it does not combine unrelated runs. Remote branches remain unchanged.

Cleanup keeps unfinished, changed, locked, or unintegrated workers and reports
why. Worker commits must be present in the result by ancestry or equivalent
cherry-picked changes. A newly checked-out user branch is preserved.

Before removing a worker, MEOW copies ignored `.meow/` runtime evidence to a
unique directory under the main repository's `.meow/cleanup/`. Plans, reports,
and local configuration remain available there. Other ignored files preserve
the worker so secrets or user files cannot disappear unnoticed. Archive failures
also preserve the worker. Cleanup never force-removes a worktree.

`meow worktree clean ID` remains the separate command for removing a result
worktree after checking its completion and push state.

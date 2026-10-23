# Verification and test selection

The built-in workflows run configured lint/test/build/browser commands inside
each fix round, before AI review. A required failure skips review and becomes
feedback for the next generator turn. Advisory failures remain visible.
Final checks reuse results only while configuration and existing source remain
unchanged; report-only additions are tolerated. Resume reruns commands because
the reuse baseline is in memory. Source edits/deletions invalidate evidence.

## AI tester

The reviewer reads code; the AI tester explores behavior with Bash after a
passing review. Configured `[[tester.tests]]` commands enable automatic testing
when workflow `auto_test` is true. Automatic selection skips prose-only changes
to docs and top-level README/CHANGELOG/LICENSE files. Code, tests, skill
instructions, configuration, and unknown/empty diffs retain the tester.
Deterministic test commands still run when the AI tester is skipped.

`--test` explicitly enables the tester regardless of that risk selection.
Quick sets `auto_test = false`, so it requires `--test` for an AI tester.
Its short plan is auto-approved unless `-m` requests approval; it has no task
graph and permits at most three generate/verify/review rounds.

Without configured commands, the tester discovers the project's test setup
and reports what it ran. `[tester] write_tests = true` permits new test files,
while existing implementation and tests remain read-only to the tester.

## Disable tests for one run

```bash
meow run "Update the API" --force-disable-tests
meow review --plan .meow/plans/api.plan.md --fix --force-disable-tests
```

`--force-disable-tests` overrides explicit and automatic testing. It disables
the AI tester, configured test commands, browser checks, and their dev servers,
while preserving lint and build. It changes runtime configuration only.
Status lists skipped gates; they are not represented as passing tests.
The policy persists on resume and propagates through Jira/background runs and
orchestrator children and final verification. Native requests using this flag
route to CLI. Independently configured remote pipeline jobs retain their policy.

## Lint hooks

The optional Claude Code lint hook and SDK edit hooks record content/config
hashes, timestamps, and success/failure in `.meow/lint-ledger.jsonl`.
The `verify` step can use that ledger instead of duplicate lint only when:

- The Claude lint installation is active and its manifest is valid.
- Every lint entry is per-file.
- Every changed file matching lint has successful evidence for its current
  content and the same lint configuration, including timeout.

Missing/malformed evidence, deletions, read errors, or project-wide lint
commands fall back to full lint. Installing a hook alone proves nothing.
SDK hooks write the same evidence, allowing CLI runs to benefit when the
installation is active. Status reports `hook-verified` or `run`.
Final checks reuse current verify results; otherwise they run full lint.
Shaping and plan reminder/capture hooks were removed. Reinstalling cleans old
manifest-owned entries, while uninstall still removes owned entries from older
versions. Core plan lifecycle and run ownership remain independent of hooks.

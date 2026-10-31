---
name: test
description: Use when testing existing or just-built code, especially a frontend, and fixing what the tester finds in a test, fix, review loop. CLI only.
---

# test

Runs the shipped `test-fix` workflow: the AI tester explores the code first, failures
are fixed, then review and the tester must both pass in one round (up to 4 rounds).
It tests existing code, has no plan step, and never delivers: nothing is pushed.
Named workflows execute through CLI mode only. Dependency or install problems:
[dependencies](../_shared/dependencies.md).

## Before starting

1. Need something to test: the user's request, or a non-empty `git diff`. If there
   is neither, ask what to test and stop. Do not ask for routes or flows: the tester
   discovers pages, edge cases and states from the diff and code. Mention that naming
   specific pages or flows narrows its focus, but is optional.
2. Frontend changes need a page to load: run the tester setup below first.
3. Pick the checkout, and tell the user which you chose:
   - Uncommitted changes exist: add `--no-worktree`, because a worktree is built from
     a commit and would miss the code under test and the running dev server. Warn
     first that fixes edit the checkout in place and stay uncommitted; suggest
     committing or stashing before starting.
   - Clean tree, or a named committed branch: use the default worktree so fixes are
     isolated. The tester's `base_url` points at the main checkout's server, so the
     tester must start its own; warn about this.
   - An explicit user request for either choice wins.

## Tester setup

Shared with `/meow:onboard`. MEOW infers the dev server, URL and browser from the
project's Playwright config and `package.json`, including an inner `frontend/` folder
([what is read](../../docs/TESTER.md)), so most projects need no entry.

1. Run `meow native verify --work-dir "<project-path>" --no-lint --preflight`. The JSON
   has `preflight` (per check: `status`, `detail`, `fix`, `snippet`), `preflight_ok`
   and `inferred` (values read from the project, with sources).
2. All `ok`: say what was inferred and continue; nothing to configure.
3. A config `fail` (`target`, `browser_config`): show that check's `snippet`, ask only
   for what cannot be detected (the dev-server command, and its port flag when the
   framework is unknown; never a port number), write it after an explicit yes, then
   re-run. Machine-specific keys (`browser_*`) go in `.meow/config.local.toml`, the
   rest in `.meow/config.toml`. Stop after 3 iterations and report what is missing.
4. An environment `fail` (`runtime`, `playwright`, `ports`, `browser`, `page`): relay
   `detail` and `fix`; do not edit config for it.
5. While any check fails, do not start the workflow.

## Run

```bash
meow run "<what to test>" --workflow test-fix --test --work-dir "<project-path>"
```

Add `--no-worktree` per the rule above. Add `--force-disable-tests` only if the user
asks to skip testing; the tester step is then skipped, never reported as passing.
Stream progress to the user and report each round: tester result (its report starts
with a "Test plan" listing discovered targets and edge cases), the fix applied, and
the review result. Report the report file paths printed on success.

## Outcomes

- Success: test and review passed in one round, then final checks ran. Say what was
  fixed and what the tester covered; do not claim delivery or a merged branch.
- Failure after 4 rounds: report the last tester and review findings and the
  continuation command. Inspect with `meow status ID`; continue with
  `meow resume ID --continue`. Never claim the code is verified.

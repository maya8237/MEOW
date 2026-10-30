# Plugin skills

Claude Code skills from the MEOW plugin. Each has a CLI equivalent; flags are in [CLI.md](CLI.md).

## /meow:run

Build a feature, resolve Jira issues, or coordinate an objective into one reviewed delivery.
Example: `/meow:run Add retry with backoff to the HTTP client`. [Reference](../skills/run/SKILL.md)

A worktree run opens an MR after pushing, then verifies its CI
pipeline. `--target BRANCH` picks the target (default `[delivery].target_branch`);
`--from` only chooses where work starts. Only `Verified by pipeline` confirms CI
verification. Targets, `--no-mr`, and provider setup: [PIPELINE.md](PIPELINE.md).

Give MEOW the change, expected behavior, and checks. A short request works:

```text
/meow:run Add a dark mode toggle to the settings page
```

A detailed prompt works better:

```text
/meow:run Add a password reset flow. Users request a reset link by email, the
link expires after 30 minutes and works once, and the new password must meet the
existing password rules. Show the same confirmation message whether or not the
email exists. Add tests for expired, reused, and invalid links.
Start from the release branch and open the MR into dev.
```

Here the work starts from `release` and the MR targets `dev`.

```text
/meow:run Add rate limiting to the public API: 100 requests per minute per API
key, with a clear error when the limit is hit. Show me the plan and wait for my
approval before you build anything.
```

Asking to approve the plan first pauses the run for your approval.

```text
/meow:run Fix duplicate submissions when a user double-clicks Save on the profile
form. Reproduce the bug, disable Save while the request is pending, and allow
retry after a failure. Add regression tests for double-clicks and failed requests.
```

```text
/meow:run Add two independently scoped features: email/password sign-in with
sign-out and protected pages, and an audit log for account changes. Keep secrets
out of logs and test access permissions. Use full sprint orchestration and
deliver one reviewed branch and MR.
```

## /meow:plan

Write a sprint plan and testable Sprint Contract before implementation.
Example: `/meow:plan Add rate limiting to the public API`. [Reference](../skills/plan/SKILL.md)

## /meow:review

Review existing code or fix review findings for a prompt, issue, MR, branch, or plan.
Example: `/meow:review`. [Reference](../skills/review/SKILL.md)

## /meow:lint

Find and fix the configured lint findings in the current session.
Example: `/meow:lint`. [Reference](../skills/lint/SKILL.md)

## /meow:test

Test existing code, fix what the tester finds, and review, until both pass. The tester
discovers its own targets, checks that pages render, and probes edge cases.
Example: `/meow:test Check the checkout page`. [Reference](../skills/test/SKILL.md)

## /meow:onboard

Optional: inspect or change detected settings, skills, integrations, and optional
features. Normal tasks perform local setup automatically; no onboarding command
is required. Existing explicit settings are preserved.
Example: `/meow:onboard`. [Reference](../skills/onboard/SKILL.md)

## /meow:customize

Create, override, attach, or remove skills, agents, and workflows in any scope.
Example: `/meow:customize`. [Reference](../skills/customize/SKILL.md)

## /meow:integrate-ci-review

Add a code review job to GitHub Actions or GitLab CI. It reviews proposed changes
against the branch you’re merging into and saves a PASS/FAIL report as a
downloadable CI artifact. Optionally post inline comments on pull/merge requests.
The job reports findings without applying code fixes.
Example: `/meow:integrate-ci-review`. [Reference](../skills/integrate-ci-review/SKILL.md)

## /meow:schedule

Schedule a request, workflow, or issue for later, or list, reschedule, and cancel triggers.
Example: `/meow:schedule run "Update the API docs" at 09:00`. [Reference](../skills/schedule/SKILL.md)

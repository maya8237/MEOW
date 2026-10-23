# Plugin skills

Claude Code skills from the MEOW plugin. Each has a CLI equivalent; flags are in [CLI.md](CLI.md).

## /meow:run

Build a feature, resolve Jira issues, or coordinate an objective into one reviewed delivery.
Example: `/meow:run Add CSV export`. [Reference](../skills/run/SKILL.md)

Worktree runs open a PR (GitHub) or MR (GitLab) after pushing, then verify its CI
pipeline and fix failing MEOW review jobs within the configured round limit.
`--target BRANCH` overrides `[delivery].target_branch`; auto-onboarding sets it
to existing `dev`, otherwise `master`. `--from` only chooses where work starts.
The selected target survives resume. Explicit in-place runs keep their delivery
choices; `--no-pr` disables MR creation. Failed creation reports a manual link.
Only `Verified by pipeline` confirms CI verification. See
[MR and pipeline verification](PIPELINE.md) for provider setup and outcomes.

Give MEOW the change, expected behavior, and checks. Examples:

```text
/meow:run Add CSV export to the Orders page. Export only the currently filtered
rows, include column headers, and escape commas and quotes. Show an empty state
when there are no results. Add tests for filters, empty results, and quoted values.
--from release --target dev
```

Here the work starts from `release` and the PR/MR targets `dev`.

```text
/meow:run Fix duplicate submissions when a user double-clicks Save on the profile
form. Reproduce the bug, disable Save while the request is pending, and allow
retry after a failure. Add regression tests for double-clicks and failed requests.
```

```text
/meow:run Add two independently scoped features: email/password sign-in with
sign-out and protected pages, and an audit log for account changes. Keep secrets
out of logs and test access permissions. Use full sprint orchestration and
deliver one reviewed branch and PR.
```

## /meow:plan

Write a sprint plan and testable Sprint Contract before implementation.
Example: `/meow:plan Add CSV export`. [Reference](../skills/plan/SKILL.md)

## /meow:review

Review existing code or fix review findings for a prompt, issue, PR/MR, branch, or plan.
Example: `/meow:review`. [Reference](../skills/review/SKILL.md)

## /meow:lint

Find and fix the configured lint findings in the current session.
Example: `/meow:lint`. [Reference](../skills/lint/SKILL.md)

## /meow:onboard

Run once per project to configure its settings, skills, integrations, and optional
features. Later tasks reuse that setup. Rerun after upgrading to review available
features or add/change setup; existing settings are preserved.
Example: `/meow:onboard`. [Reference](../skills/onboard/SKILL.md)

## /meow:customize

Create, override, attach, or remove skills, agents, and workflows in any scope.
See what each user, project, and local scope customizes with `meow ui`.
Example: `/meow:customize`. [Reference](../skills/customize/SKILL.md)

## /meow:migration

Convert a legacy MEOW project layout to the current layout.
Example: `/meow:migration`. [Reference](../skills/migration/SKILL.md)

## /meow:integrate-ci-review

Add a code review job to GitHub Actions or GitLab CI. It reviews proposed changes
against the branch you’re merging into and saves a PASS/FAIL report as a
downloadable CI artifact. Optionally post inline comments on pull/merge requests.
The job reports findings without applying code fixes.
Example: `/meow:integrate-ci-review`. [Reference](../skills/integrate-ci-review/SKILL.md)

## /meow:schedule

Schedule a request, workflow, or issue for later, or list, reschedule, and cancel triggers.
Example: `/meow:schedule run "Add CSV export" at 09:00`. [Reference](../skills/schedule/SKILL.md)

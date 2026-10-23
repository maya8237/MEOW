# Plugin skills

Claude Code skills from the MEOW plugin. Each has a CLI equivalent; flags are in [CLI.md](CLI.md).

## /meow:run

Build a feature, resolve Jira issues, or coordinate an objective into one reviewed delivery.
Example: `/meow:run Add CSV export`. [Reference](../skills/run/SKILL.md)

Standard runs commit and push after checks pass; you open the PR/MR yourself.
Full sprint orchestration also opens one PR (GitHub) or MR (GitLab) for the
combined result by default. If creation fails, MEOW reports the reason and a
manual link; `--no-pr` disables creation. Provider setup is in
[ORCHESTRATION.md](ORCHESTRATION.md#delivery).

Give MEOW the change, expected behavior, and checks. Examples:

```text
/meow:run Add CSV export to the Orders page. Export only the currently filtered
rows, include column headers, and escape commas and quotes. Show an empty state
when there are no results. Add tests for filters, empty results, and quoted values.
```

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

Set up a project or rerun after upgrading MEOW to review available features.
Preserve existing settings and configure only selected additions or changes.
Example: `/meow:onboard`. [Reference](../skills/onboard/SKILL.md)

## /meow:customize

Create, override, attach, or remove skills, agents, and workflows in any scope.
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

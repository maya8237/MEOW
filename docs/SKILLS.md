# Plugin skills

Claude Code skills from the MEOW plugin. Each has a CLI equivalent; flags are in [CLI.md](CLI.md).

## /meow:run

Build a feature, resolve Jira issues, or coordinate an objective into one reviewed delivery.
Example: `/meow:run Add CSV export`. [Reference](../skills/run/SKILL.md)

## /meow:plan

Write a sprint plan and testable Sprint Contract before implementation.
Example: `/meow:plan Add CSV export`. [Reference](../skills/plan/SKILL.md)

## /meow:review

Review existing code or fix review findings for a prompt, issue, PR/MR, branch, or plan.
Example: `/meow:review`. [Reference](../skills/review/SKILL.md)

## /meow:lint

Find and fix the configured lint findings in the current session.
Example: `/meow:lint`. [Reference](../skills/lint/SKILL.md)

## /meow:schedule

Schedule a request, workflow, or issue for later, or list, reschedule, and cancel triggers.
Example: `/meow:schedule run "Add CSV export" at 09:00`. [Reference](../skills/schedule/SKILL.md)

## /meow:onboard

Add integrations and optional features to a project, or set up one never onboarded.
Example: `/meow:onboard`. [Reference](../skills/onboard/SKILL.md)

## /meow:customize

Create, override, attach, or remove skills, agents, and workflows in any scope.
Example: `/meow:customize`. [Reference](../skills/customize/SKILL.md)

## /meow:migration

Convert a legacy MEOW project layout to the current layout.
Example: `/meow:migration`. [Reference](../skills/migration/SKILL.md)

## /meow:integrate-ci-review

Add MEOW's CI review job to GitHub Actions or GitLab CI.
Example: `/meow:integrate-ci-review`. [Reference](../skills/integrate-ci-review/SKILL.md)

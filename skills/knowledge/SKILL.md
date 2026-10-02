---
name: knowledge
description: Run MEOW's report-only project knowledge audit/check or explicitly create selected knowledge documents.
---

Use `meow knowledge audit -d <project>` to inspect missing guidance and broken
links. It is report-only and never creates or edits project files. Use
`meow knowledge check` for deterministic structural status; advisory prose
drift does not fail the check. Only run `meow knowledge create --finding ID`
after showing the user the audit and receiving explicit selection. Existing
files are preserved unless `--overwrite` is explicitly selected.

Use this skill when onboarding an unfamiliar repository, reviewing whether
architecture/domain/security/reliability guidance is missing, checking links,
or investigating possible documentation/code drift. It is not an automatic
gate for `meow plan` or `meow run`.

# Update check

At the start of every public `/meow:*` skill invocation, run
`meow --check-update` once, including when using the skill's native mode.
If it warns that a newer MEOW version is available, relay the warning and
tell the user to run `meow --update` (or `meow -u`), then continue the requested
task. Version checks and updates use the latest published release tag.
Do not install an update automatically. An unavailable version server does
not block the task. If `meow` is missing, follow the skill's installation
guidance instead.

Public CLI calls check automatically. Individual `meow native ...` helpers
and internal workers do not check, so keep this check at the skill entry point.

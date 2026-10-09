# Contributing to MEOW

Thanks for helping make software delivery simpler.

## Before you start

MEOW is built around the Claude Agent SDK and Claude Code skills. Read
[AGENTS.md](AGENTS.md), then install the development dependencies:

```bash
python -m pip install -e ".[dev]"
```

## Make a change

1. Open an issue for a larger behavior change, or explain the small change in
   the pull request.
2. Add or update a focused test before changing behavior.
3. Keep the CLI, Claude Code skill, and documentation paths aligned.
4. Run the project checks:

   ```bash
   python -m pytest -q
   ruff check
   ```

5. Update the README, changelog, or integration docs when user-facing behavior
   changes.

## Pull requests

Keep pull requests focused and describe the user-visible result. Include the
commands you ran and call out any integration that needs credentials or a live
service. Small documentation, demo, and good-first-issue contributions are
welcome; maintainers will add the `good first issue` label when an item is
scoped for a new contributor.

Please do not commit credentials, local `.meow` state, generated checkpoints,
or private project data.

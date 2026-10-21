# Dependencies and installs

MEOW installs Node dependencies when it creates a worktree. Missing modules,
CLIs, or browsers are yours to fix; do not wait for the user.

- Install what is missing from the project's lockfile with its own package
  manager: `bun install --linker isolated`, `npm ci`,
  `pnpm install --frozen-lockfile`, `yarn install --immutable`. Browsers are
  separate: `bunx playwright install`. This is not a source edit, so read-only
  and verdict-only roles may do it too.
- If an install times out, run it yourself and raise `install_timeout`
  (seconds) under `[worktree_setup]` in `.meow/config.local.toml` so MEOW's own
  installs allow it next time.
- Repair `node_modules` any other legitimate way (reinstall, `--force`, clear a
  half-built folder).
- Never set `linker = "hoisted"`, turn off `globalStore`, or add `--production`
  to get past a failure. Declare the missing import in `package.json`, or
  report it.

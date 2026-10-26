# Worktree setup and dependencies

## Explicit setup

`[worktree_setup]` applies before agents in SDK/native feature, issue,
branch-review, plan-only, and task worktrees.

```toml
[worktree_setup]
copy = [".env.example"]
commands = [["make", "setup"]]
install_timeout = 1800  # optional: seconds per dependency install (default 900)
```

Copies must be regular, non-secret-like project-relative files; absolute paths,
`..`, links, and secret-like names are rejected. Commands are literal argument
arrays run in the new worktree with a 300-second timeout. Onboarding previews
the exact commands. The `setup` role's `WorktreeSetup` permission can deny or
require approval; runs stop for either decision.

Agents with `Bash` may install missing dependencies, repair `node_modules`, and
raise `install_timeout` themselves; they must not switch Bun to `hoisted`.

Failures retain the worktree and `.meow/worktree-setup.pending` for retry.
Successful setup is not repeated on ordinary reuse; completed copies are
accepted when bytes match. Completed actions are journaled.

## Node dependencies

After explicit setup, missing dependencies are installed before agents,
including in reused worktrees and in-place runs. Nested apps are detected;
workspace members install together from their workspace root.
Worktrees do not share a writable `node_modules`.

`packageManager` in `package.json` overrides lockfile detection; ambiguous
lockfiles require an explicit manager. Bun, npm, pnpm, and Yarn/PnP are supported.
Existing lockfiles use frozen/immutable installs (`npm ci` for npm); absent
lockfiles use regular installs. Commands obey the `setup` permission role,
retain diagnostics, and fail clearly when the manager is missing from PATH.

## Bun shared storage

The installer offers a default-yes user-wide shared-store option, preserving
other `~/.bunfig.toml` settings while setting `linker = "isolated"` and
`globalStore = true`. Bun's default cache selects the store location;
`globalDir` is unrelated. Use a Bun version supporting `install.globalStore`.
Project settings/environment may override these user settings.

MEOW's own Bun installs also set `BUN_INSTALL_GLOBAL_STORE=1`, so the store works
without the installer step. A project or user `globalStore = false` or
`linker = "hoisted"` is respected: MEOW prints a notice (and logs it) instead of
overriding it. A caller's own `BUN_INSTALL_GLOBAL_STORE` value is kept.
`meow --update` offers the option once (interactive only, skipped when
`~/.bunfig.toml` already enables it) and migrates registered repos.

Registered and newly onboarded Bun repositories, nested apps, and explicit
Bun projects inside workspaces are discovered/listed and migrated in a detached
background process. The installer reports PID/log; migration may outlive Done.
It never switches npm/pnpm/Yarn or creates/edits project `bunfig.toml`.
Existing project configs warn because they can override user configuration.

Migration runs `bun install --force --linker isolated`, adding
`--frozen-lockfile` when a Bun lockfile exists. A frozen-lockfile error permits
one retry without that flag, which may update the lockfile; other errors do not
retry. Every command obeys setup permissions. Failure in one folder does not
stop others; diagnostics include a retry command.

For additional repos, run at the install root:

```sh
bun install --force --linker isolated --frozen-lockfile
```

Omit the frozen flag without a lockfile. Even cached packages need a local
install to create each worktree's links; disabling shared storage keeps normal
package-manager cache/install behavior.
See [Bun's documentation](https://bun.com/docs/pm/global-store).

The store contains live dependencies. `bun pm cache rm` breaks project links
until reinstall. MEOW checks local and transitive link targets, reinstalls
missing dependencies, then verifies the result. Incomplete installs retry once
with `--force` under setup policy and fail if still broken; healthy worktrees
skip installation. Isolated linking can expose undeclared imports: declare the
dependency in that folder's `package.json`.

## Stale and unmigrated installs

After each install MEOW records a fingerprint (manager, lockfiles, declared
dependencies) in `.meow/node-install.fingerprint`. A changed fingerprint
reinstalls; a healthy install with no record is kept and baselined. A new
worktree still runs one quick install to create its links.
A Bun project with a hoisted `node_modules` (no `node_modules/.bun`) gets a hint
with the conversion command; MEOW never converts it outside the installer,
`meow --update`, and `/meow:onboard`, which asks first.

## Bun install guarantees

- Worktree installs pass `--linker isolated`, which outranks any user or
  project bunfig, so a stray `hoisted` cannot disable the shared store. Only a
  project's own `bunfig.toml` with `linker = "hoisted"` opts out (logged).
- Older Bun without `--linker` retries once without it.
- `production = true` in a bunfig leaves devDependencies out; they are not
  treated as missing, so no needless forced reinstall.
- A forced install that still leaves packages missing fails naming them.
  Agents must not switch the linker or disable `globalStore` to get past it.
- The 900-second install timeout is set with `install_timeout` (seconds) in
  `[worktree_setup]`, or `MEOW_NODE_INSTALL_TIMEOUT` when unset; logs record each
  install's duration. A timed-out install is
  retried next time because `.meow/node-install.pending` remains.
- Notices (printed and logged) cover a project `hoisted` linker, `globalStore = false`, or
  `production = true` (devDependencies such as Playwright stay uninstalled;
  Bun cannot override it from the command line, so remove the setting).

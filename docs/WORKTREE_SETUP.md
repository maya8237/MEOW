# Worktree setup and dependencies

## Explicit setup

`[worktree_setup]` applies before agents in SDK/native feature, issue,
branch-review, plan-only, and task worktrees.

```toml
[worktree_setup]
copy = [".env.example"]
commands = [["python", "-m", "pip", "install", "-e", "."]]
```

Copies must be regular, non-secret-like project-relative files; absolute paths,
`..`, links, and secret-like names are rejected. Commands are literal argument
arrays run in the new worktree with a 300-second timeout. Onboarding previews
the exact commands. The `setup` role's `WorktreeSetup` permission can deny or
require approval; auto stops for either decision.

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

## Bun install guarantees

- Worktree installs pass `--linker isolated`, which outranks any user or
  project bunfig, so a stray `hoisted` cannot disable the shared store. Only a
  project's own `bunfig.toml` with `linker = "hoisted"` opts out (logged).
- Older Bun without `--linker` retries once without it.
- `production = true` in a bunfig leaves devDependencies out; they are not
  treated as missing, so no needless forced reinstall.
- A forced install that still leaves packages missing fails naming them.
  Agents must not switch the linker or disable `globalStore` to get past it.
- The 900-second install timeout is set with `MEOW_NODE_INSTALL_TIMEOUT`
  (seconds); logs record each install's duration. A timed-out install is
  retried next time because `.meow/node-install.pending` remains.
- Warnings are logged for a project `hoisted` linker or `globalStore = false`.

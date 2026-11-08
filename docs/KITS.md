# Kits

A kit is a git repository (GitHub, GitLab, self-hosted, or a local path) with
any of `skills/`, `agents/`, `workflows/`, and `hooks/`, plus an optional
`meow-kit.toml`. The kit does not choose its scope; you do when you install it.

```bash
meow kits add acme/meow-kit                              # GitHub shorthand
meow kits add gitlab:platform/tools/meow-kit             # GitLab, subgroups allowed
meow kits add https://gitlab.example.com/team/meow-kit   # any URL, .git optional
meow kits add git@gitlab.example.com:team/meow-kit.git   # SSH uses your keys
meow kits add URL --scope project --ref v1.2.0 --only skills,agents
```

`add` fetches the kit, lists what it contains (including the command each hook
runs), asks to confirm (`--yes` skips), then saves and publishes it. Other
commands: `meow kits list | show NAME | update [NAME] [--check] [--accept] |
uninstall NAME [--scope S] [--keep-cache]`.

## Fetching

MEOW runs plain `git` against the clone URL, so your own login (credential
helper, SSH keys, token) covers private repos and it never prompts for a
password. `git ls-remote` resolves the ref to a commit; a shallow
`git fetch --depth 1 URL SHA` downloads it (a full clone is the fallback). The
commit is stored once in `~/.meow/kits/` without `.git`. Only `https`, `ssh`,
`git`, and local paths are allowed; kits containing symlinks are rejected.

## Scopes

| | User (default) | Project | Local |
|---|---|---|---|
| Who gets it | You, every project | Everyone on the repo | You, this project |
| Declared in | `~/.meow/config.toml` | `.meow/config.toml` | `.meow/config.local.toml` |
| Commit pin | `~/.meow/kits.lock` | `rev` in `config.toml` (committed) | `.meow/local/kits.lock` |
| Updates | Automatic | Reported; `meow kits update`, then commit | Automatic |

Lock files are private to a machine and git-ignored. A project kit is pinned
by `rev` in the committed config, so a teammate downloads exactly that commit
at the next Claude Code start and an upgrade is a normal reviewed diff:

```toml
[[kits]]
name = "team-kit"
url = "gitlab:acme/meow-kit"
ref = "main"                  # what to follow
rev = "9f3c2a1…40 characters" # written by MEOW; project scope only
update = "auto"               # or "pinned": never fetched at startup
only = ["skills", "agents"]   # optional
auto_accept_hooks = false     # user and local scope only
check_every = "6h"            # optional: 30m, 6h, 1d
config = false                # true applies the kit's [config]
```

Precedence is local > project > user > builtin. Within a scope your own files
beat a kit's; two kits defining one name in a scope is an error.
`meow native custom` lists overrides.

## Updates

At Claude Code start, `meow sync --auto` asks each due kit's remote for its
commit (`git ls-remote`, no download) at most every `check_every`. A change in
a user or local kit applies at once; offline failures are silent and the cached
copy keeps working. `meow kits update` always checks.

Skills, agents, and workflows are prompts and apply automatically. Hooks are
shell commands, so a changed hook stays on the old commit until you run
`meow kits update NAME --accept`, which prints what it would run
(`auto_accept_hooks = true` skips this). Project kits never move on their
own: startup reports "update available".

## Skills for built-in roles

```toml
# meow-kit.toml
name = "acme-kit"
[skills.roles]
fix-with-tests = ["review_fixer"]   # only this role; unlisted skills go to all
```

Roles are validated ([list](CUSTOM.md#skills-and-agents)). A skill grants no
tools; `[permissions]` stays yours.

## Kit configuration

With `config = true`, `meow-kit.toml` may set only values that run no code:

```toml
[config.workflow]
default = "strict-review"
[config.models]
reviewer = "opus"
[config.worktree_setup]
install_timeout = 1800
```

`[permissions]`, `[[lint]]`, `[build]`, `[tester]`, the rest of
`[worktree_setup]` (`copy`, `commands`), `[delivery]`, and MCP are rejected. A
kit's config ranks below the file that declares it.

## Install and uninstall

`install.sh --kit URL` (`install.ps1 -kit URL`; repeatable) installs MEOW and
adds user kits, and the installer prompts for one in a terminal. On an
existing setup use `meow kits add`: the installer also updates MEOW itself.
The [uninstaller](INSTALL.md#uninstall) asks about each user kit.
`meow kits uninstall` removes the config entry, lock entry, published skills
and hooks, and cached commits no lock pins. Editing the files by hand
works too; startup sync notices.

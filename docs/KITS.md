# Kits

A kit is a git repository (GitHub, GitLab, self-hosted) or, for kit authors, a
local folder (see [Local kits](#local-kits)), with
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

## Local kits

While writing a kit, use the folder directly; it needs no git and no commit:

```bash
meow kits add ./my-kit --yes   # also ../kit, ~/kits/my-kit, or a non-git absolute path
meow sync                      # after editing the folder: republish skills/hooks
```

The folder is used in place, never copied or pinned, so edits apply on the next
sync and `meow kits update` has nothing to fetch. It exists only on your
machine, so user (default) and local scopes work and `--scope project` is
refused. Hooks and `claude_agent` agents need no `--accept`: it is your code.
An absolute path to a git repository is still fetched as a git kit.
`meow kits uninstall` never deletes your folder.

## Fetching

MEOW runs plain `git` against the clone URL, so your own login (credential
helper, SSH keys, token) covers private repos and it never prompts for a
password. `git ls-remote` resolves the ref to a commit; a shallow
`git fetch --depth 1 URL SHA` downloads it (a full clone is the fallback). The
commit is stored once in `~/.meow/kits/` without `.git`. Only `https`, `ssh`,
`git`, and local git repositories are allowed; kits containing symlinks are rejected.

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
(`auto_accept_hooks = true` skips this).

An agent with `claude_agent = true` is also synced to Claude Code, which can
delegate to it on its own, so it must declare `tools`. In a user or local kit, a
new such agent or one that gains a tool waits for `--accept` too, and keeps its
earlier copy meanwhile; narrowing tools and prose changes apply at once. A project
kit's reviewed `rev` bump counts as approval. See [registration](CUSTOM.md#claude-code-registration).

### Project kits

Project kits never move on their own. When a check finds a newer commit MEOW
remembers it: `meow kits list` shows `update available`, and every Claude Code
start prints a `Kit updates available:` block until it is applied. Then one
person updates the pin:

```bash
meow kits update team-kit   # rewrites rev in .meow/config.toml, applies hook changes
git diff .meow/config.toml  # review what moved, then commit it
```

Teammates receive the new `rev` with the commit. CI or a pre-push hook can run
`meow kits update --check`, which prints pending updates and exits 1 when any
kit is behind.

## Runs keep their kit versions

A `meow run`, `plan`, or `review` records the kit commits in effect (`kit_pins`
in its run journal) and its processes and workers keep using them, so a kit
update from another session cannot change a run midway. `meow resume
--continue` re-applies the recorded commits, downloading one again if it was
pruned, and prints which version it uses. If a commit cannot be restored
(remote unreachable, kit since removed) it warns and uses the installed
version. Cleanup keeps commits that unfinished runs in the project still pin.
Not covered: in-session `/meow:*` flows (each `meow native` call is its own
process) and the driver of `--orchestrate` runs (each task run pins when it
starts).

## Writing a kit

```bash
meow kits init my-kit        # scaffold: manifest, a skill, an agent, README
meow kits validate .         # check a folder (or a git URL) before publishing
```

`validate` reports every error at once (bad manifest, skills whose name
differs from the folder, agents missing a prompt, unknown workflow agents,
symlinks) and lists each hook with the command it will run, so reviewers see
what the kit executes. It exits 1 on any error. Then push the repo and
`meow kits add owner/my-kit`.

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

`meow onboard --kit URL` sets a kit up for a project in one command.
`install.sh --kit URL` (`install.ps1 -kit URL`; repeatable) installs MEOW and
adds user kits, and the installer prompts for one in a terminal. On an
existing setup use `meow kits add`: the installer also updates MEOW itself.
The [uninstaller](INSTALL.md#uninstall) asks about each user kit.
`meow kits uninstall` removes the config entry, lock entry, published skills
and hooks, and cached commits no lock pins. Editing the files by hand
works too; startup sync notices.

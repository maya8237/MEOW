# Optional onboarding and overrides

Start normal tasks directly: first use detects recognized project checks and
compatible Claude MCP registrations from local files. No separate onboarding
command is required. Detection never launches an agent, connects to a provider,
or installs dependencies. Worktree preparation installs dependencies when needed.
Explicit settings take precedence; empty check lists disable inference.

`meow onboard --non-interactive --work-dir PATH` refreshes base setup and
applies automatic local detection and the overrides you supply. It never prompts or starts
agents, tests, MCP servers, package installs, or schedules. The flag is explicit
for automation; `meow onboard` also runs without prompts. Use `/meow:onboard`
for conversational setup. See `meow onboard --help` for all flags.

Base setup repairs and verifies the Git ignore boundary and creates missing
shared/local configs. Flags and `--config FILE` merge into ignored
`.meow/config.local.toml`, preserving comments and unrelated settings. They take
precedence over shared/user config. Existing optional features stay configured
when their flags are omitted; compatible MCP registrations can be referenced
automatically, but connections start only when an operation uses them.
Invalid configuration returns 1 and restores the previous local config.
The JSON result contains changed paths, configured/skipped feature names, and
`connection_tested: false`; it never includes credential values. Configured
does not mean connected. Use `meow native verify --work-dir PATH` to check
launcher readiness, then a real MCP tool call to confirm authentication.

## Integrations

Reuse an existing Claude stdio MCP registration (Bash):

```bash
meow onboard --non-interactive --work-dir /path/to/project \
  --set 'jira.mcp.from="atlassian"' --jira-project-key PROJ
```

With one clear registered provider match, first use selects the reference
automatically. Select explicitly when several match. MEOW resolves the current
server settings when used and stores no copied credentials. Hosted OAuth
connectors require a supported stdio registration for detached CLI execution.
An explicit Jira issue key needs no configured project key. Latest-issue lookup
uses the configured project or the only accessible project; ambiguity needs a choice.

GitLab and GitHub:

```bash
meow onboard --non-interactive --work-dir /path/to/project \
  --set 'gitlab.mcp.from="gitlab"'

meow onboard --non-interactive --work-dir /path/to/project \
  --set 'github.mcp.from="github"'
```

Choose the MCP launcher/package already used by your project; onboarding does
not install it. Each provider accepts `--PROVIDER-command` and repeatable
`--PROVIDER-arg`. Use `--jira-arg=--flag` for arguments starting with `-`.
Legacy command/argument/environment settings remain supported. Review URLs
are supplied later to `meow review`.

## Other optional settings

Common flags include `--target BRANCH`, `--docs-dir PATH`, `--max-rounds N`,
`--write-tests` / `--no-write-tests`, and repeatable `--lint-command`,
`--test-command`, and `--build-command`. Repeated commands replace the local
list for that type. Explicit test commands enable Tester mode on every run;
detected tests run as deterministic checks. Set `[tester] auto_test = false`
to run explicit checks without the AI tester. Onboarding executes neither.

`--set KEY=TOML_VALUE` supports the remaining config fields, including arrays
and inline tables. Quote the entire assignment so the shell preserves TOML:

```bash
meow onboard --non-interactive --work-dir /path/to/project \
  --set 'models.reviewer="sonnet"' \
  --set 'tester.tests=[{command="python -m pytest", timeout=300}]' \
  --set 'worktree_setup.copy=["assets"]' \
  --set 'orchestrator.max_parallel=2'
```

For larger configurations, use `--config options.toml`, with the same fields as
the [config template](../templates/meow-config.toml.example). It is a merge,
not a replacement; arrays supplied explicitly replace their local list.
Precedence within one command: config file, named flags, then `--set` in order.
All imported settings stay local, including credentials. Explicitly selected
worktree commands and other executable settings take effect during later runs;
onboarding does not execute them.

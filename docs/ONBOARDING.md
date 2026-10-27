# Flag-based onboarding

`meow onboard --non-interactive --work-dir PATH` refreshes base setup and
configures only the optional settings you supply. It never prompts or starts
agents, tests, MCP servers, package installs, or schedules. The flag is explicit
for automation; `meow onboard` also runs without prompts. Use `/meow:onboard`
for conversational setup. See `meow onboard --help` for all flags.

Base setup repairs and verifies the Git ignore boundary and creates missing
shared/local configs. Flags and `--config FILE` merge into ignored
`.meow/config.local.toml`, preserving comments and unrelated settings. They take
precedence over shared/user config. Existing optional features stay configured
when their flags are omitted; new optional integrations stay disabled.
Invalid configuration returns 1 and restores the previous local config.
The JSON result contains changed paths, configured/skipped feature names, and
`connection_tested: false`; it never includes credential values. Configured
does not mean connected. Use `meow native verify --work-dir PATH` to check
launcher readiness, then a real MCP tool call to confirm authentication.

## Integrations

Jira Cloud example (Bash):

```bash
meow onboard --non-interactive --work-dir /path/to/project \
  --jira-project-key PROJ --jira-url https://example.atlassian.net \
  --jira-username user@example.com --jira-token '$JIRA_API_TOKEN' \
  --jira-command uvx --jira-arg mcp-atlassian
```

Single-quoted `$JIRA_API_TOKEN` stores an environment reference; set the variable
in the environment that runs MEOW. A literal token is also accepted and saved
only in ignored local config. Environment references avoid recording token
values in shell history. `--jira-personal-token` configures Server/Data Center
authentication instead. Omitted credential flags preserve existing settings.
Jira needs a project key and MCP command from flags or existing config.

GitLab and GitHub:

```bash
meow onboard --non-interactive --work-dir /path/to/project \
  --gitlab-url https://gitlab.example.com --gitlab-token '$GITLAB_TOKEN' \
  --gitlab-command uvx --gitlab-arg mcp-gitlab

meow onboard --non-interactive --work-dir /path/to/project \
  --github-token '$GITHUB_PERSONAL_ACCESS_TOKEN' \
  --github-command /path/to/github-mcp --github-arg=--stdio
```

Choose the MCP launcher/package already used by your project; onboarding does
not install it. Each provider accepts `--PROVIDER-command` and repeatable
`--PROVIDER-arg`. Use `--jira-arg=--flag` for arguments starting with `-`.
GitLab/GitHub require an MCP command from flags or existing config. Review URLs
are supplied later to `meow review`.

## Other optional settings

Common flags include `--target BRANCH`, `--docs-dir PATH`, `--max-rounds N`,
`--write-tests` / `--no-write-tests`, and repeatable `--lint-command`,
`--test-command`, and `--build-command`. Repeated commands replace the local
list for that type. Configured test commands enable Tester mode on every run;
onboarding records them without executing them.

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

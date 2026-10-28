---
name: review
description: Use when reviewing existing code or fixing review findings against a prompt, Jira issue, PR/MR, branch, plan, saved review, or CI checkout. Defaults to native, except CI and tester flows.
---

# review

Runs **natively** by default. Read [the shared protocol](../_shared/native-mode.md)
first. Use **CLI mode** when requested, or for CI and tester flows. Dependency or install problems: [dependencies](../_shared/dependencies.md).
Test suppression uses CLI with `--force-disable-tests`; it overrides `--test`,
disables the tester/test/browser gates, and retains lint/build checks.

## Pick the source (exactly one) and the mode

**Source** — whatever the user gave:

| The user gave... | Source | Local checkout? |
|---|---|---|
| Free text describing what to check | prompt | current checkout, as-is |
| A Jira issue key (or "the latest issue") | jira | current checkout, as-is -- the issue's text becomes the review basis, nothing gets built |
| A GitLab merge request URL | gitlab | **none** -- read-only, diff fetched via MCP |
| A GitHub pull request URL | github | **none** -- read-only, diff fetched via MCP |
| A bare MR/PR number ("MR 42", "PR #42") | gitlab or github | as above; take the provider and project from the `origin` remote, and ask if it is neither |
| A GitLab pipeline checkout, or an explicit `--ci` request | ci | **none** -- detached, report-only CLI review |
| A branch name + a target branch | branch | isolated worktree (default) or in place |
| A plan file path, or nothing at all and a plan exists in `docs_dir` | plan | current checkout, as-is |
| An existing review file to resume | review-file | whatever that file's own flavor needs (plan/prompt only -- see step 5) |
| Nothing at all, and no plan exists either | prompt (empty) | current checkout -- reviews the git diff, or the whole project if the diff is empty |

Only one source may be given. The `ci` source is CLI-only because it validates
the live GitLab checkout and CI environment; run `meow review --ci` even when
this native-default skill was invoked. If the user seems to want two at once (e.g. a
prompt *and* a branch), ask which one they mean.

**Mode** — did the user ask to *check/grade* the code, or to *fix* it?
"review", "check", "grade", "does this pass" -> report-only. "fix", "make it
pass", "loop until it's clean" -> fix mode. Default to report-only if
genuinely unclear; it's the safer, non-destructive choice. Fix mode is
unavailable for the gitlab or github source (no local checkout to fix -- say
so and either fall back to reporting or ask the user to check the remote
branch out locally first). Resuming from a review file always fixes (there's no
"resume but don't act on it" case).

## Native mode

For a whole-project request (including "from scratch", "from zero", or "from 0"),
use the prompt source with `--scope project`, even when a plan or dirty diff
exists. Inspect every area in the code-owned inventory and include the grounded
`COVERAGE:` records requested by the generated prompt. `native verdict` rejects
missing or stale coverage.

Follow the shared protocol's lint/check ordering, durable-job handling,
host/model resolution, and fresh reviewer dispatch. Local PASS needs current
verification and coverage bindings; remote reviews use only the supplied diff.

1. Pick source and mode as above. The project uses `.meow/config.toml` at
   its root.
2. Resolve the working directory and, for jira/gitlab/github, fetch the source
   material:
   - **prompt** / **plan** / no source: `meow native prepare --no-worktree
     --allow-dirty --work-dir "<project-path>"`. For the plan source,
     resolve the plan file first: the one the user named, else `meow
     native latest-plan --work-dir "<project-path>"`; if neither exists
     (no path given, none found), fall back to the prompt source with an
     empty prompt (reviews the git diff / whole project).
   - **jira**: a named issue needs no configured `project_key`. For latest-issue
     lookup, use the configured or only accessible project; ask if ambiguous.
     Fetch through the Jira MCP tools already connected to
     this session (tool names containing `jira`) -- not `[jira.mcp]`. If
     none connected, say so and stop. Need `key`,
     `summary`, `description`. Build the review basis text: `Review
     whether the current code satisfies Jira issue <key>: <summary>\n\n
     <description>` -- this is exactly what `--focus` carries into the
     `reviewer-prompt` role below; there is no separate Jira review role.
     Then `meow native prepare --no-worktree --allow-dirty --work-dir
     "<project-path>"` (no worktree -- reviewing current code, not
     building from the ticket).
   - **gitlab** / **github**: fetch title, description, and diff through that
     provider's connected MCP tools, not its config launcher. If unavailable,
     report and stop. Do not check out the remote branch. Prepare with
     `meow native prepare --no-worktree --allow-dirty --work-dir "<project-path>"`.
   - **ci**: do not use native mode. Run `meow review --ci` from the detached
     GitLab checkout; use `--target-ref` and `--artifact-dir` only when the
     pipeline or maintainer explicitly supplies them. The command is
     report-only and validates `CI_COMMIT_SHA`, the supported pipeline type,
     the detached checkout, and the target history before reviewing.
   - **branch**: both branch and target are required -- never guess a
     target. Isolated worktree (default): `meow native prepare
     --existing-branch "<branch>" --name "branch-review-<sanitized-branch>"
     --allow-dirty --work-dir "<project-path>"` (sanitize the same way a
     feature name normally is: non `[A-Za-z0-9._-]` characters -> `-`). In
     place (user asked for it): `meow native prepare --existing-branch
     "<branch>" --no-worktree --allow-dirty --work-dir "<project-path>"`
     -- fails clearly if `<branch>` isn't actually checked out there. Either
     way fails clearly if `<branch>` doesn't exist locally or as
     `origin/<branch>` -- report and stop.
   - **review-file**: the file the user named, else `meow native
     latest-review --work-dir "<project-path>"` (gives `review_file` and
     `flavor`). `flavor: gitlab`, `github`, or `branch` cannot be resumed here
     (no target/checkout to re-diff against) -- tell the user to re-run
     this skill with the gitlab/github/branch source instead, or address the
     feedback directly, and stop. `meow native prepare --no-worktree
     --allow-dirty --work-dir "<project-path>"` for `max_rounds`.
3. **Report-only** (every source but review-file, which always fixes): one
   reviewer subagent, no round counter.
   - **gitlab**/**github** (always report-only, never fix mode): `meow native
     prompt reviewer-mr --provider <gitlab|github> --work-dir
     "<project-path>"`. The JSON's `query` is null for this role; build the
     task message from its `query_template` by replacing `<title>`,
     `<description>`, and `<diff>` with the fetched values, then dispatch one
     reviewer subagent with `system_prompt` followed by that message.
   - **prompt**/**jira**: `meow native prompt reviewer-prompt --focus
     "<basis>"` (omit `--focus` for an empty prompt).
   - **plan**: `meow native prompt reviewer-plan --plan <plan_file>`.
   - **branch**: `meow native prompt reviewer-branch --target "<target>"
     --branch "<branch>" [--worktree]`.
   Dispatch one reviewer subagent per the shared protocol, then `meow
   native verdict <review_file>`; report PASS/FAIL and the review file
   path. Do not edit any code.
4. **Fix mode**: anchor on the plan path or first reviewer prompt's report path;
   keep that anchor. Reset only for a new review. At each round start, advance
   once; stop if exhausted. Round 1 reviews first; later rounds fix verified
   findings, lint, then get a fresh reviewer and validate.
   - **plan** source fixes as the plan's **generator** (Sprint-Contract-
     aware, `meow native prompt generator --plan <plan_file>`); reviews
     with `prompt reviewer-plan --plan <plan_file> [--focus "<prompt>"]`.
   - **prompt**/**jira** sources fix as a scoped fixer (`meow native
     prompt review-fixer`; smallest edit per finding, no scope creep);
     review with `prompt reviewer-prompt --focus "<basis>"` (jira's basis
     is the text built in step 2; prompt's is the user's own text).
   - **branch** source fixes as a scoped fixer (same as prompt/jira);
     reviews with `prompt reviewer-branch --target "<target>" --branch
     "<branch>" [--worktree]` (pass `--worktree` when step 2 used the
     isolated-worktree path) -- this recomputes the branch's diff fresh
     every round, so it sees each round's fixes without needing a commit.
   Report PASS, or, if exhausted, the review file's path and remaining
   findings.
5. **Resuming a review file** (always fixes): read it (`meow native
   verdict <review_file>`). Before accepting PASS, run current required
   checks. For an old report without verification/coverage bindings, generate
   a fresh reviewer prompt and review the current checkout first.
   A new focus requires a fresh review even when the saved report passed.
   If the new focus requests the whole project, use project scope for the
   fresh review and all subsequent rounds; an old diff scope must not narrow it.
   Anchor on the saved plan path or prompt report. Inspect `round <anchor>
   --show`; never reset on resume. If no counter exists, advance once to count
   the saved review. Stop if `round >= max_rounds`; otherwise
   advance once at the next round start. The saved review already counts.
   Preserve the focus through remaining rounds.
6. If an isolated worktree was used (branch source), remind the user
   their own checkout was left untouched and where the worktree lives.

## CLI mode

When the user requests tester mode, or asks to run tests or validate behavior,
for an explicit plan, pass `--test` to
`meow review --plan PATH --test` (optionally with `--fix`). This uses the
CLI orchestration so configured servers remain available to the tester agent.
Tester mode is not available for implicit plan discovery or other review
sources. Without `--test`, keep the native flow above.

```bash
meow review "<prompt>" --work-dir "<project-path>"              # prompt source, report-only
meow review --scope project --fix --work-dir "<project-path>"  # review and fix every repository area
meow review --fix "<prompt>" --work-dir "<project-path>"        # prompt source, loop to max_rounds
meow review --jira [KEY] [--fix] --work-dir "<project-path>"
meow review --gitlab "<mr-url>" --work-dir "<project-path>"     # always report-only
meow review --github "<pr-url>" --work-dir "<project-path>"     # always report-only
meow review --ci [--target-ref "<ref>"] [--artifact-dir "<path>"]  # GitLab CI, report-only
meow review --branch "<branch>" --target "<target>" [--fix] [--no-worktree] --work-dir "<project-path>"
meow review --plan "<path>" [--fix] --work-dir "<project-path>"   # omit for auto-discovery
meow review --plan "<path>" --test [--fix] --work-dir "<project-path>"
meow review --review-file "<path>" ["<focus prompt>"] --work-dir "<project-path>"  # always fixes
```

Omitting every source flag reviews the latest plan in `docs_dir` (default
`.meow/plans`), falling back to the git diff (or whole project) if none exists.
`--gitlab` and `--github` combined with `--fix` are rejected -- there is no
local checkout to fix. `--ci` is mutually exclusive with prompt text, other
sources, `--fix`, `--test`, and `--no-worktree`; it reviews the exact detached
CI checkout and writes report/verdict artifacts. Stream `[reviewer]`/`[generator]`/
`[review_fixer]` progress; report PASS or, after `max_rounds`, the review
file path.

## Continuation

On entry, inspect saved run IDs with `meow status ID --json` (detach any
`meow resume --parked`). A command exiting `75` (or printing `MEOW_RETRY`): wait
`retry_after`, then run its `next_command` detached (host background execution), or
rerun the same native helper, up to `managed_resumes`. Exit 1 /
`MEOW_BLOCKED`: stop and report. Resume only when `meow status ID --json` gives
no stop reason. Full rules: [native protocol](../_shared/native-mode.md#the-meow-native-helper).

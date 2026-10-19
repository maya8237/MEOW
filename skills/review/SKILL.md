---
name: review
description: Review existing code -- report-only, or loop fixing until it passes -- sourced from a free-text prompt, a Jira issue, a GitLab merge request, a GitHub pull request, a GitLab CI checkout, a local branch's diff against a target, an existing plan file, or an existing review file being resumed. Runs natively in this Claude Code session by default except for the CLI-only CI source. Use whenever the user wants code checked or fixed against some existing source of truth, rather than a new feature built from scratch.
---

# review

One consolidated review operation covering prompt, issue, remote-review,
branch, plan, review-file, and CI sources. Runs **natively** by default. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) first. Use **CLI mode** (bottom) only if explicitly
asked.

## Pick the source (exactly one) and the mode

**Source** — whatever the user gave:

| The user gave... | Source | Local checkout? |
|---|---|---|
| Free text describing what to check | prompt | current checkout, as-is |
| A Jira issue key (or "the latest issue") | jira | current checkout, as-is -- the issue's text becomes the review basis, nothing gets built |
| A GitLab merge request URL | gitlab | **none** -- read-only, diff fetched via MCP |
| A GitHub pull request URL | github | **none** -- read-only, diff fetched via MCP |
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

Complete the scoped audit, authorized fixes, and focused proofs first; freeze
source/config. Do not add audit passes while final checks run.

Before accepting a local PASS, run `meow native review-checks --background` with the
same `--work-dir` and `--active-dir` as the reviewer. Inspect
`meow native review-checks --status` until the durable job finishes, then use
`meow native verdict <review_file>` to validate the report. Required tests and
builds block PASS alongside required lint. The JSON supplies durable log/result
paths. After source/config edits, rerun checks and generate a fresh review
prompt. Remote reviews remain report-only against the supplied diff.

Keep one job ID; a lost shell handle is not a failed job. If a run exceeds its
baseline, inspect logs, child start times and source/config changes for retries.
Isolate artifact-writing tests; preserve user files. Restart only for confirmed
stale/failed evidence.

Pass `--host codex` in Codex (or the actual host name elsewhere) to every
`native prompt` call. If exact supported model IDs are known, repeat
`--available-model ID` for each. Dispatch using the returned `model` only when
non-null; null means inherit the host model. Surface `model_fallback_reason`
when present. Never invent mappings between providers' model names.

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
   - **jira**: the project's `.meow/config.toml` must have `[jira]` with
     `project_key`. Fetch through the Jira MCP tools already connected to
     this session (tool names containing `jira`) -- not `[jira.mcp]`. If
     none connected, or `[jira]` missing, say so and stop. Need `key`,
     `summary`, `description`. Build the review basis text: `Review
     whether the current code satisfies Jira issue <key>: <summary>\n\n
     <description>` -- this is exactly what `--focus` carries into the
     `reviewer-prompt` role below; there is no separate Jira review role.
     Then `meow native prepare --no-worktree --allow-dirty --work-dir
     "<project-path>"` (no worktree -- reviewing current code, not
     building from the ticket).
   - **gitlab**: fetch title/description/diff through the GitLab MCP tools
     already connected to this session (tool names containing `gitlab`) --
     not `[gitlab.mcp]`. If none connected, say so and stop. Do not check
     anything out. `meow native prepare --no-worktree --allow-dirty
     --work-dir "<project-path>"` (changes nothing; confirms config).
   - **github**: fetch title/description/diff through the GitHub MCP tools
     already connected to this session (tool names containing `github`) --
     not `[github.mcp]`. If none connected, say so and stop. Do not check
     anything out. `meow native prepare --no-worktree --allow-dirty
     --work-dir "<project-path>"` (changes nothing; confirms config).
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

When the user requests tester mode for an explicit plan, pass `--test` to
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

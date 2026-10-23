# MR and pipeline verification

After `builtin.deliver` pushes a plain `meow run --from BRANCH`, MEOW opens an
MR/PR into `BRANCH`, validates the pipeline file, waits for the pipeline, and
fixes code until the `meow-review` job passes. Any workflow containing
`builtin.deliver` gets this. Without `--from`, nothing changes. Scheduled runs
and `--jira` do not support `--from`. GitLab (`.gitlab-ci.yml`) and GitHub
(`.github/workflows/*.yml`) are supported.

## Flow

1. Push the verified branch (never undone by later steps).
2. Open the MR/PR; failure records `pr_error` and a manual link.
3. Validate the pipeline file; invalid files are fixed (`lint_fixer`).
4. Wait for the pipeline of the pushed commit.
5. If the review job failed, fix, push, and wait again (`review_fixer`).
6. Journal the outcome in `delivery.pipeline` and print the final line.

Waiting and judging are deterministic code; only fixes use agents. A fix that
changes nothing ends the loop as not verified. Fix pushes go to the run branch
only, inside the deliver step.

## Flags and config

| Flag | Effect |
|---|---|
| `--no-pr` | Skip the MR and pipeline verification |
| `--fix-non-meow-jobs` | Also fix other failing jobs (requires `--from`) |

`[pipeline]` in `.meow/config.toml`:

| Key | Default | Meaning |
|---|---|---|
| `open_mr` | `true` | `false` equals `--no-pr` |
| `fix_non_meow_jobs` | `false` | Equals `--fix-non-meow-jobs` |
| `max_fix_rounds` | `3` | Fix rounds before giving up |
| `start_timeout` | `300` | Seconds to wait for a pipeline to appear |
| `timeout` | `3600` | Seconds to wait for the pipeline |
| `poll_seconds` | `15` | Seconds between polls |

## Tokens

Environment first: `MEOW_GITLAB_TOKEN` or `MEOW_GITHUB_TOKEN`; then
`[gitlab.mcp.env].GITLAB_TOKEN` or `[github.mcp.env].GITHUB_PERSONAL_ACCESS_TOKEN`.
The token must read pipelines and jobs. Without one the run ends
`Not verified by pipeline: ...` with a manual MR link; the push stays.

## Pipeline file validation

- GitLab: the CI lint API on `.gitlab-ci.yml`.
- GitHub: `actionlint` when installed; otherwise reported as not validated,
  which does not block.

## Which jobs are fixed

- `meow-review` / `meow_review`: decided by job status, so a failure with
  `allow_failure: true` in a green pipeline is still fixed.
- Other failing jobs are only reported, unless `--fix-non-meow-jobs`.
- Jobs whose name or stage matches `deploy|release|publish|rollback`
  (case-insensitive) are never fixed.

## Status lines

Printed as `[delivery] ...` and mirrored to `delivery.stage` (`meow status`):

- `Feature complete. MEOW loop finished; entering final delivery (MR into BASE).`
- `MR opened: URL. Waiting for the pipeline to verify the delivery.`
- `Pipeline running on SHA7 (fix round N of M).`
- `meow review job failed; fixing (round N of M).`
- `Pipeline file invalid; fixing (round N of M).`
- `Merge request and pipeline verification skipped (--no-pr / open_mr=false).`

## Outcomes

| Outcome | Final line | Meaning |
|---|---|---|
| verified | `Verified by pipeline: URL` | Review job passed; nothing unfixed failed |
| unvalidated | `Verified by pipeline (pipeline file not validated: ...)` | As verified, but the file could not be linted |
| not verified | `Not verified by pipeline: REASON` | Failing jobs left, rounds exhausted, review job canceled or skipped, timeout, fix push failed, no token, MR not opened |
| unverified | `Not verified by pipeline: REASON` | The review job could not run (auth, infra); no code edits |
| no pipeline | `Not verified by pipeline: no pipeline started ...` | No CI file or MR pipelines disabled |

Only `Verified by pipeline` means verified. Never describe other outcomes as
verified or done-and-verified.

## Not automatic

- A not-verified run is complete: `meow resume --continue` does not retry it.
  Fix the failing jobs on the MR branch, or re-run the pipeline.
- A custom workflow `timeout` on the `deliver` step cancels the stage and fails
  the already-pushed run; do not set one.
- Native sessions have no fix loop: they open the MR with connected tools and
  report the pipeline status. See [native mode](../skills/_shared/native-mode.md).

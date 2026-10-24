# MR and pipeline verification

After `builtin.deliver` pushes a worktree run, MEOW opens an
MR into its saved target, validates the pipeline file, waits for the pipeline, and
fixes code until the `meow-review` job passes. Any workflow containing
`builtin.deliver` gets this. Explicit in-place runs retain their delivery choices
without opening an MR. GitLab (`.gitlab-ci.yml`) and GitHub
(`.github/workflows/*.yml`) are supported.

## Flow

1. Push the verified branch (never undone by later steps).
2. Open the MR; failure records `pr_error` and a manual link, printed as
   `Open it manually: LINK` on the final line.
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
| `--no-mr` (alias `--no-pr`) | Skip the MR and pipeline verification (also applies to orchestration) |
| `--target BRANCH` | Override the configured MR target for this run |
| `--from BRANCH` | Start work from this branch; does not set the MR target |
| `--fix-non-meow-jobs` | Also fix other failing jobs in a worktree run |

The MR target is `--target`, otherwise `[delivery].target_branch`. Auto-onboarding
saves `dev` if it exists locally or on origin, otherwise `master`. Existing config
values are preserved; interactive onboarding offers to change the target.
The same fallback applies to older configs without this setting. The current
checkout and `--from` never override the MR target. The target is printed and
saved before work starts, including background runs; resume keeps the saved value.

```bash
meow run "Add a dark mode toggle" --from release --target dev
```

`[pipeline]` in `.meow/config.toml`:

| Key | Default | Meaning |
|---|---|---|
| `open_mr` | `true` | `false` equals `--no-mr` |
| `fix_non_meow_jobs` | `false` | Equals `--fix-non-meow-jobs` |
| `max_fix_rounds` | `3` | Fix rounds before giving up |
| `start_timeout` | `300` | Seconds to wait for a pipeline to appear |
| `timeout` | `3600` | Seconds to wait for the pipeline |
| `poll_seconds` | `15` | Seconds between polls |

## Tokens

Environment first: `MEOW_GITLAB_TOKEN` or `MEOW_GITHUB_TOKEN`; then
`[gitlab.mcp.env].GITLAB_TOKEN` or `[github.mcp.env].GITHUB_PERSONAL_ACCESS_TOKEN`.
The token must read pipelines and jobs. Without one the run ends
`Not verified by pipeline: ...` naming the variable, plus the MR URL; the push stays.

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
- `Merge request and pipeline verification skipped (--no-mr / open_mr=false).`

## Outcomes

| Outcome | Final line | Meaning |
|---|---|---|
| verified | `Verified by pipeline: URL` | The pipeline passed. With a `meow-review` job that job passed; without one, no MEOW review ran in CI |
| unvalidated | `Verified by pipeline (pipeline file not validated: ...)` | As verified, but the file could not be linted |
| not verified | `Not verified by pipeline: REASON` | Failing jobs left, rounds exhausted, review job canceled or skipped, no jobs (`pipeline has no jobs`), timeout, fix push failed, no token (names the variable), MR not opened (reason plus `Open it manually: LINK`) |
| unverified | `Not verified by pipeline: REASON` | The review job could not run (auth, infra); no code edits |
| no pipeline | `Not verified by pipeline: no pipeline started for SHA7` | Nothing started within `start_timeout` (no CI file, or MR pipelines disabled) |

Only `Verified by pipeline` means verified. Never describe other outcomes as
verified or done-and-verified.

## Not automatic

- A finished not-verified run is complete: `meow resume --continue` does not
  retry it. Fix the failing jobs on the MR branch, or re-run the pipeline.
- A run interrupted mid-stage (cancelled or crashed while waiting or fixing)
  resumes with `meow resume ID --continue`: it re-enters the stage, keeps the
  round count, and does not reopen the MR.
- A custom workflow `timeout` on the `deliver` step cancels the stage and fails
  the already-pushed run; do not set one.
- Native sessions have no fix loop: they open the MR with connected tools and
  report the pipeline status. See [native mode](../skills/_shared/native-mode.md).

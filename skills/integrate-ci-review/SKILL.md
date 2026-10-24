---
name: integrate-ci-review
description: Use when adding or configuring MEOW's CI review job in GitHub Actions or GitLab CI, with optional inline PR/MR comments.
---

# Integrate CI review

Add `meow review --ci` to the repository's CI configuration. It reviews the
exact checkout and writes artifacts; it defaults to **report-only**. Edit CI files only,
never application code, and do not use `--fix` or `meow run` in the job. Dependency or install problems: [dependencies](../_shared/dependencies.md).

## Configure credentials and model defaults

Before editing CI, read [the environment guide](references/environment.md).
Ask: **"Should MEOW post inline review comments on PRs/MRs?"** Reuse a prior answer or wait for the choice.
When a matching GitHub/GitLab MCP is configured, inspect existing project CI
variables/secrets first. Reuse clear matches under their existing names through
job-local `MEOW_*` aliases. Request missing CI variables/secrets at the final
handoff, after editing and validating CI; missing values do not block that work.
Ask whether to import model fallbacks from Claude `settings.json`. Present the
environment guide's copy table at the end. Keep URL/token/key values out of YAML.
All models accept CI overrides ahead of approved local defaults. Support direct
Anthropic and optional gateways; without local settings, use CI/Claude defaults.

## Detect the CI provider

1. Read the repository's `AGENTS.md`, `CLAUDE.md`, and other applicable
   instructions before editing. Inspect the current CI files and `git status`.
2. Detect the provider from the repository, not from the user's wording:
   - GitHub Actions: `.github/workflows/*.{yml,yaml}`.
   - GitLab CI: `.gitlab-ci.yml` or `.gitlab-ci.yaml`.
3. If exactly one provider is present, use it automatically.
4. If both providers are present, do not edit either one until the user
   chooses which CI is canonical for this integration.
5. If neither provider is present, ask one concise question: `Should I set up
   GitHub Actions or GitLab CI?` After the user chooses, create the minimal
   configuration for that provider and place the job in it.

Do not treat an unrelated YAML file as CI. When several workflows/configs are
present, use the workflow or local GitLab include that already owns the main
test/lint pipeline. Ask only if there is no clear owner.

## Fit the existing pipeline

Before editing, use [the pipeline guide](references/pipeline.md) to match job naming
and ask about placement, dependencies, manual execution, install tool, cancellation, and failure.
Honor prior answers; otherwise wait. Defaults: automatic, `needs: []`, pip, cancellation
enabled, failure allowed. Include the last stage as a placement option.

The target branch is the branch being reviewed against: GitHub's `github.base_ref`
or GitLab's `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`.
Use the same project target branch in the rules, checkout, fetch, and review
command for either provider.

Update an existing `meow-review`/`meow_review` job in place to match the selected
preferences and required invariants; never create a duplicate.

### GitHub Actions

- Prefer adding the convention-matched MEOW job under `jobs:` in the main CI
  workflow. Do not create a second workflow when the current workflow can host
  the job.
- Preserve runner, checkout, and Python conventions. Use job-level `needs`,
  `concurrency`, and `continue-on-error` for the selected pipeline preferences.
- Ensure the workflow runs for the same review targets as the project. Add a
  missing `pull_request` trigger or non-target `push` trigger only when that
  matches the existing CI policy.
- GitHub Actions has no stage list. Do not invent a stage; a job is enough.
  If no existing workflow is a suitable owner, create
  `.github/workflows/meow-review.yml` with one convention-matched MEOW job.
- On pull requests, check out the source commit SHA with `fetch-depth: 0`,
  not GitHub's synthetic merge ref. Map the GitHub event fields to the
  `CI_*` variables consumed by the existing CLI before invoking it. Fetch the
  target branch into `refs/remotes/origin/<target>`.
- Use a normal `pull_request` trigger. Never use `pull_request_target` while
  checking out and executing untrusted change-set code with
  model API credentials.

The GitHub job must include the equivalent of:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@v4
    with:
      # Use the PR head SHA for pull requests, otherwise the pushed SHA.
      ref: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}
      fetch-depth: 0
  - uses: actions/setup-python@v5
    with:
      python-version: "3.12"
  - run: python -m pip install meow-harness
  - name: Run MEOW report-only review
    run: |
      # Add the selected MEOW_* exports from the environment guide here.
      git fetch --no-tags origin "refs/heads/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}:refs/remotes/origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
      meow review --ci --target-ref "refs/remotes/origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}" --artifact-dir .meow/ci-artifacts
  - uses: actions/upload-artifact@v4
    if: always()
    with:
      name: meow-review
      path: .meow/ci-artifacts/
```

Set these CI context values at the job level, using GitHub expressions and
the project's target branch:

- `CI_PIPELINE_SOURCE`: `merge_request_event` for `pull_request`, otherwise
  `push`
- `CI_MERGE_REQUEST_EVENT_TYPE`: `detached`
- `CI_MERGE_REQUEST_SOURCE_BRANCH_NAME`: `github.head_ref`
- `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`: `github.base_ref` for pull requests,
  otherwise the configured target branch (for example, `dev`)
- `CI_MERGE_REQUEST_DESCRIPTION`: the pull-request body
- `CI_MERGE_REQUEST_DESCRIPTION_IS_TRUNCATED`: `false`
- `CI_COMMIT_BRANCH`: `github.ref_name` for pushes
- `CI_COMMIT_SHA`: the checked-out PR head SHA or `github.sha` for pushes

Keep selected `MEOW_*` inputs on the review step, per the environment guide.

Keep untrusted GitHub values in environment variables and quote shell
expansions. Do not interpolate them into shell source.

### GitLab CI

- Use `templates/gitlab-ci-review.yml` in this plugin as the source of truth
  for the job's rules and behavior. When integrating another repository, copy
  and adapt the job into its local CI configuration; do not assume that the
  other repository can resolve this plugin's relative template path.
- Add one convention-matched MEOW job to the root config or local included file
  that owns the project's validation jobs.
- Preserve the template's detached merge-request and non-target branch push
  rules, full Git history, target-ref fetch, `.meow/ci-artifacts/` artifacts,
  and selected `MEOW_*` credential requirements and SDK exports.
- Offer `post-checks`/`post-review`, another tailored name, and custom-name input.
  Confirm new stage names and order before editing; preserve existing stages.
- Apply the selected dependencies, execution mode, cancellation, and failure
  policy; the template runs automatically with empty needs and allows failure.
- Keep `when: never` as the final fallback rule so unrelated pipeline types do
  not run the job.
- GitLab already supplies the target branch through
  `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`; do not copy GitHub's `base_ref`
  expression into GitLab YAML.

If the project uses a target branch other than the template's `dev`, keep the
target branch consistent across the rules, fetched ref, `--target-ref`, and
CI context. Do not silently change the review contract; explain any mismatch
between the current project and the shipped template and ask before making a
broader runtime change.

## Configure and verify

### Optional inline review notes

If enabled, add `--post-comments github` or `--post-comments gitlab` to
`meow review --ci`. Install `meow-harness` with `python -m pip install meow-harness` unless
the user opts into uv; do not add an install-spec variable or ask the user to choose the package.

- GitHub: reuse a suitable existing token through a job-local `MEOW_GITHUB_TOKEN`
  alias, or use `${{ secrets.GITHUB_TOKEN }}`. Grant the job `pull-requests: write`
  alongside `contents: read`. Actions supplies
  `GITHUB_EVENT_PATH`, `GITHUB_REPOSITORY`, and `GITHUB_API_URL`. Preserve the
  `CI_*` mappings above. Use a token with Pull requests write permission when
  supplying a personal access token instead. Fork PR tokens may be read-only.
- GitLab: reuse a suitable token through a job-local `MEOW_GITLAB_TOKEN` alias,
  or supply a masked new token with `api` scope and comment access. GitLab supplies the API URL,
  target project ID, and MR IID automatically.
- Enable posting only for PR/MR events; push jobs keep the report-only command.
  Never expose secrets by switching to `pull_request_target`.

Findings become inline discussions on added/deleted lines. Existing comments and
replies inform the review; already reported concerns stay in the report rather
than becoming new notes. The CLI checks locations and remote revisions, then
re-fetches comments to skip matching bodies at the same current diff location.
Missing tokens, unreadable/oversized history, malformed findings, or publishing
failures exit 2 and retain artifacts. Partial publishes may leave notes posted;
concurrent jobs can still race, so serialize comment-posting jobs per PR/MR.

1. Make the smallest edit that fits the existing CI layout. Preserve comments,
   anchors, includes, matrices, and unrelated jobs.
2. Validate YAML with an available parser or the provider's local validation
   tooling. Re-read the changed job and inspect the diff.
3. Confirm all of the following:
   - the job is named `meow-review`/`meow_review` exactly once;
   - it installs MEOW and runs `meow review --ci`;
   - it never includes `--fix`;
   - it checks out/fetches enough history to compute the merge base;
   - it stores `.meow/ci-artifacts/` even when the review fails;
   - API URL/credentials are CI variables/secrets, with no literal YAML defaults;
   - every model accepts an environment override before any approved fallback;
   - naming, placement, needs, execution mode, and policies match the choices; and
   - no application files or generated review fixes were changed.
4. Tell the user which CI file changed, the selected stage/order and job policies,
   imported model defaults, and the posting choice. Explain that review reports
   PASS/FAIL without fixing the change set. End with the environment guide's copy
   table and an explicit request to add missing CI variables/secrets, including
   the provider token when posting is enabled and no suitable token is available.

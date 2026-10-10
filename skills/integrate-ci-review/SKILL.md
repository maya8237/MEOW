---
name: integrate-ci-review
description: Add MEOW's report-only CI review job to an existing GitHub Actions or GitLab CI setup, or create the minimal provider configuration when no CI exists. Use when a user asks to integrate, configure, or add meow review to CI.
---

# Integrate CI review

Add the MEOW CI review job to the repository's own CI configuration. The
`--ci` flag selects the exact CI checkout as the review source; that path is
always **report-only**: it runs `meow review --ci`, writes the review artifacts,
and never uses `--fix`, `meow run`, or an edit-capable review mode. The CLI
rejects `--ci` combined with `--fix`.

The skill may edit CI configuration, but it must not edit application code or
turn the CI review into an automatic fixer.

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

In this skill, the target branch is the branch being reviewed against. GitHub
calls it the pull request's **base ref** (`github.base_ref`); GitLab calls it
the merge request's **target branch** (`CI_MERGE_REQUEST_TARGET_BRANCH_NAME`).
Use the same project target branch in the rules, checkout, fetch, and review
command for either provider.

Look for an existing `meow-review`/`meow_review` job or a step that already
runs `meow review --ci`. If one exists, update it in place only when it is
missing a required invariant; never create a duplicate.

### GitHub Actions

- Prefer adding a `meow-review` job under `jobs:` in the existing main CI
  workflow. Do not create a second workflow when the current workflow can host
  the job.
- Preserve the workflow's existing runner, checkout, Python setup, and job
  ordering conventions. Add `needs` only when the existing pipeline clearly
  requires review to wait for a validation job.
- Ensure the workflow runs for the same review targets as the project. Add a
  missing `pull_request` trigger or non-target `push` trigger only when that
  matches the existing CI policy.
- GitHub Actions has no stage list. Do not invent a stage; a job is enough.
  If no existing workflow is a suitable owner, create
  `.github/workflows/meow-review.yml` with one `meow-review` job.
- On pull requests, check out the source commit SHA with `fetch-depth: 0`,
  not GitHub's synthetic merge ref. Map the GitHub event fields to the
  `CI_*` variables consumed by the existing CLI before invoking it. Fetch the
  target branch into `refs/remotes/origin/<target>`.
- Use a normal `pull_request` trigger. Never use `pull_request_target` while
  checking out and executing untrusted change-set code with
  `ANTHROPIC_API_KEY`.

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
  - run: python -m pip install meow
  - name: Run MEOW report-only review
    run: |
      git fetch --no-tags origin "refs/heads/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}:refs/remotes/origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
      meow review --ci --target-ref "refs/remotes/origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}" --artifact-dir .meow/ci-artifacts
  - uses: actions/upload-artifact@v4
    if: always()
    with:
      name: meow-review
      path: .meow/ci-artifacts/
```

Set these environment values at the job level, using GitHub expressions and
the project's target branch:

- `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`
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

Keep untrusted GitHub values in environment variables and quote shell
expansions. Do not interpolate them into shell source.

### GitLab CI

- Use `templates/gitlab-ci-review.yml` in this plugin as the source of truth
  for the job's rules and behavior. When integrating another repository, copy
  and adapt the job into its local CI configuration; do not assume that the
  other repository can resolve this plugin's relative template path.
- Add one `meow_review` job to the local root config or the local included file
  that owns the project's validation jobs.
- Preserve the template's detached merge-request and non-target branch push
  rules, full Git history, target-ref fetch, `.meow/ci-artifacts/` artifacts,
  and `ANTHROPIC_API_KEY` secret requirement.
- Use an existing validation stage when one is clearly appropriate, such as
  `test`, `quality`, `verify`, `checks`, or `review`. Add a new `review` stage
  only when the config has an explicit `stages:` list with no suitable stage
  and a separate review stage fits its ordering. If no `stages:` list exists,
  use GitLab's existing default `test` stage instead of adding one.
- Keep `when: never` as the final fallback rule so unrelated pipeline types do
  not run the job.
- GitLab already supplies the target branch through
  `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`; do not copy GitHub's `base_ref`
  expression into GitLab YAML.

The job's command remains report-only:

```yaml
script:
  - meow review --ci --artifact-dir "$CI_PROJECT_DIR/.meow/ci-artifacts"
```

If the project uses a target branch other than the template's `dev`, keep the
target branch consistent across the rules, fetched ref, `--target-ref`, and
CI context. Do not silently change the review contract; explain any mismatch
between the current project and the shipped template and ask before making a
broader runtime change.

## Configure and verify

1. Make the smallest edit that fits the existing CI layout. Preserve comments,
   anchors, includes, matrices, and unrelated jobs.
2. Make the integration idempotent: a second invocation must find and update
   the existing job rather than append another one.
3. Validate YAML with an available parser or the provider's local validation
   tooling. Re-read the changed job and inspect the diff.
4. Confirm all of the following:
   - the job is named `meow-review`/`meow_review` exactly once;
   - it installs MEOW and runs `meow review --ci`;
   - it never includes `--fix`;
   - it checks out/fetches enough history to compute the merge base;
   - it stores `.meow/ci-artifacts/` even when the review fails;
   - it references `ANTHROPIC_API_KEY` as a secret/CI variable, never as a
     committed value; and
   - no application files or generated review fixes were changed.
5. Tell the user which CI file changed, which stage/job owns the integration,
   and that they must provide `ANTHROPIC_API_KEY` as a masked GitLab variable
   or GitHub Actions secret. Mention that the job reports PASS/FAIL and does
   not fix the change set.

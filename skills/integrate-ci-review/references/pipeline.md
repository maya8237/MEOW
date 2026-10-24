# Pipeline preferences

Read the effective CI configuration, including local includes, before asking.
Honor preferences already supplied in the conversation. Ask the remaining
questions before editing CI and wait for answers; defaults are suggested choices,
not permission to skip questions.

Name the job `meow-review` or `meow_review` to match existing job IDs in the owning
pipeline (hyphens or underscores). For mixed conventions, follow nearby validation
jobs; if neither is established, use `meow_review` for GitLab or `meow-review` for
GitHub. Update an existing MEOW job in place, adjusting references if renamed.

## Placement

For GitLab, show the resolved stages in order and ask: **"Where should MEOW run?"**
Offer existing stages by name, explicitly including **"Last existing stage
(<name>)"**, plus **"New stage: post-checks"**, **"New stage: post-review"**,
one additional repository-specific name, and a separate **"Enter custom stage
name"** option. Generate the additional suggestion from the pipeline's vocabulary
and naming style; choose a name not already suggested or present in the pipeline
(for example, `code-analysis` or `code_analysis` when appropriate). Display the
actual proposed name, not a generic "custom suggestion" label.
Match the stage naming convention: use
`post_checks`/`post_review` for underscores or `post-checks`/`post-review` for
hyphens. When stage names have no established separator, follow the job naming
convention above. Reuse a suggested stage if it already exists; do not duplicate it.
If there is no explicit stage list, show GitLab's defaults: `build`, `test`,
`deploy`. Offer ordinary named stages instead of the built-in `.post` stage.

For any suggested new stage, ask: **"Where should it go: first, before/after
which existing stage, or last?"** For **Enter custom stage name**, ask for the name too.
Wait for both the name and position. Show the resulting order and insert the stage.
When introducing `stages:`, preserve the effective stages, including GitLab's
defaults when previously implicit. Keep existing stage order and unrelated jobs.

For GitHub Actions, ask whether MEOW should run alongside validation, after
specified jobs, or **last (after all existing jobs)**; do not invent GitLab stages.
Use the dependency question below to confirm the chosen predecessors.

## Dependencies and manual execution

Ask: **"Which previous stages or jobs should MEOW depend on?"** Offer
**"No jobs (default: `needs: []`)"**, the existing earlier stages, and eligible
job IDs. Resolve selected stages into their jobs and write the selected job IDs
to `needs`. With no dependencies, write `needs: []` explicitly, including when
MEOW is placed in the last stage: it can start immediately rather
than waiting for earlier stages. Explain this when presenting placement choices.
Only selected dependencies delay MEOW; failed prerequisites can prevent it running.
Check conditional jobs and use optional needs only where absence is expected.

Ask: **"Should the MEOW review job be manual? (Default: no)"**
Offer **"No, run automatically (default)"** and **"Yes, start it manually"**.
For GitLab, automatic means matching rules use `when: on_success`; manual uses
`when: manual`. Preserve the final `when: never` fallback. Retain explicit
`allow_failure: true` by default: a manual job must be optional, not block the
pipeline waiting for someone to start it. If the user chooses manual execution
with `allow_failure: false`, explain that this creates a blocking manual gate.
See [GitLab's manual job reference](https://docs.gitlab.com/ci/jobs/job_control/#types-of-manual-jobs).

GitHub has no job-level `when: manual`. If manual execution is selected, explain
that it needs a manually dispatched workflow and confirm its workflow and PR/ref
inputs before editing; preserve the checkout/CI context and MR/PR-only posting
contract. Keep automatic execution unless the user chooses this alternative.

The same review job posts inline comments when enabled, by adding
`--post-comments gitlab` or `--post-comments github` for MR/PR events. Manual
execution also postpones comment posting until someone starts the review job.
Do not create a separate posting job.

## Install tool

Ask: **"Install MEOW with uv instead of pip? (Default: no)"** Offer **"No, use pip
(default)"** and **"Yes, use uv when it is available"**. The package stays
`meow-harness`. For uv, replace the install line with this guarded command so a
runner without uv still installs with pip:
`if command -v uv >/dev/null 2>&1; then uv pip install --system meow-harness; else python -m pip install meow-harness; fi`
On GitHub add `astral-sh/setup-uv@v5` after `setup-python` and run it with
`shell: bash`; on GitLab rely on the image's own uv, if it has one.

## Cancellation and failure

Ask both questions, recommending **Yes** for each:

- **"Should MEOW be interruptible and stop its older review when a new commit
  starts a pipeline for the same MR/PR? (Default: yes)"**
- **"Should MEOW be allowed to fail without failing the pipeline?
  (Default: yes)"**

### GitLab

The default configuration is:

```yaml
workflow:
  auto_cancel:
    on_new_commit: interruptible

meow_review:
  needs: []
  interruptible: true
  allow_failure: true
```

Merge the workflow setting into the owning configuration without replacing its
rules or other settings. Inspect matching `workflow:rules:auto_cancel` and job
rule overrides so the effective policy matches the answer. `interruptible: true`
alone is insufficient: with `conservative`, an already started non-interruptible
job prevents cancellation of the old review. With `interruptible`, only jobs
marked interruptible are canceled, even after a deployment starts.

Explain when asking that this workflow policy also applies to other interruptible
jobs in the affected pipelines. Keep unrelated jobs' interruption settings intact.
If cancellation is declined, set this job's `interruptible: false` and preserve
the existing workflow cancellation policy. If allowing failure is declined, set
`allow_failure: false`. Do not mask command failures with `|| true`.

For GitLab versions without `workflow:auto_cancel`, explain the limitation and
check project auto-cancel settings; do not claim interruption alone guarantees
cancellation. A newer pipeline for the same ref must actually be created.
See [GitLab's cancellation reference](https://docs.gitlab.com/ci/yaml/#workflowauto_cancelon_new_commit).

### GitHub Actions

Default to job-level `continue-on-error: true` and `concurrency` with
`cancel-in-progress: true`. Use a stable group such as
`meow-review-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}`;
never include the commit SHA or run ID, which would prevent supersession.
Keep cancellation at the MEOW job level so other workflow jobs retain their policy.
If declined, use `continue-on-error: false` or `cancel-in-progress: false`
respectively. Keep concurrency for serializing inline comment posting.
See [GitHub's concurrency reference](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

## Verify

Check job naming, stage/order, `needs`, automatic/manual mode, and job policies,
including inherited or rule-level overrides. Validate YAML and confirm that a
new commit would match the same MR/PR/ref cancellation scope. Report configuration
validation separately from a live superseding-commit test; do not claim a remote
job was canceled without observing it.

export const meta = {
  name: 'default',
  description: "MEOW's default workflow: plan, check the plan, implement, verify, review and fix, then deliver.",
}

// Mirrors src/meow/project/custom/builtin/workflows/default.toml (plan ->
// plan-check -> approve -> tasks -> rounds[generate, verify, review] ->
// checks -> deliver) using the `meow native` helpers, so runs keep MEOW's
// journals, gates and delivery. Agents run the commands; this script only
// sequences them.
//
// Usage: /meow:default <request>
//   or args { request, name, noWorktree, from, target, stopAfterPlan, plan }
//
// Pause and add notes: stop the run (`x` in /workflows), write the notes to
// .meow/workflow-notes.md in the project, then ask Claude to relaunch this
// workflow. Prompts never change, so finished steps replay from saved results
// and every remaining step reads the notes.

const input = typeof args === 'string' ? { request: args } : args || {}
const request = String(input.request || '').trim()
if (!request) {
  throw new Error('Pass the feature request, e.g. /meow:default Add CSV export to reports')
}

const NOTES = '.meow/workflow-notes.md'
const RULES = [
  'You are one step of a MEOW workflow run. Use only the `meow native` helpers',
  'named below for MEOW state; they print JSON. Never commit, push, merge or',
  'open a PR/MR unless this step says so. If a helper exits 75 (MEOW_RETRY),',
  'wait its `retry_after` and rerun it, at most 3 times. On any other failure',
  'report it in `error` and stop.',
  `If ${NOTES} exists in the project root, read it first: it holds notes the`,
  'user added while this run was paused. Follow them; where they conflict with',
  'the plan or earlier feedback, the notes win.',
].join('\n')

const errorField = { type: 'string', description: 'Empty on success; otherwise what failed.' }
const dirs = p => `--work-dir "${p.project_dir}" --active-dir "${p.active_dir}"`

function stop(step, error, extra = {}) {
  log(`Stopped at ${step}: ${error}`)
  return { status: 'stopped', step, error, ...extra }
}

// ---------------------------------------------------------------- prepare
phase('Prepare')
const prepareFlags = [
  input.name ? `--name "${input.name}"` : '--name "<a short slug of the request>"',
  input.noWorktree ? '--no-worktree' : '--fresh',
  input.from ? `--from "${input.from}"` : '',
].filter(Boolean).join(' ')

const prep = await agent(
  `${RULES}

Prepare a MEOW run for this request:
<request>
${request}
</request>

1. In the project root (the current directory; report it as project_dir), run
   \`meow native verify --work-dir .\`. If \`tester.configured\` is true, set
   tester_required to true and stop: tester runs need \`/meow:run\` (CLI mode).
2. Run \`meow native prepare ${prepareFlags} --work-dir .\`. If it exits
   nonzero, put its message in error and stop. Report onboarding.files if any.
3. Run \`meow native checkpoint preparing --intent run --request "<the request>"
   ${input.target ? `--target "${input.target}" ` : ''}--work-dir <project_dir> --active-dir <active_dir>\`
   and keep its run_id.
Return the JSON fields from prepare and the checkpoint.`,
  {
    label: 'prepare',
    schema: {
      type: 'object',
      required: ['error', 'project_dir', 'active_dir', 'plan_file', 'review_file', 'max_rounds', 'use_worktree', 'run_id', 'tester_required'],
      properties: {
        error: errorField,
        project_dir: { type: 'string' },
        active_dir: { type: 'string' },
        plan_file: { type: 'string' },
        review_file: { type: 'string' },
        max_rounds: { type: 'integer' },
        use_worktree: { type: 'boolean' },
        run_id: { type: 'string' },
        tester_required: { type: 'boolean' },
        onboarding_files: { type: 'array', items: { type: 'string' } },
      },
    },
  },
)
if (!prep) return stop('prepare', 'agent stopped')
if (prep.tester_required) return stop('prepare', 'tester is configured; use /meow:run (CLI mode) for this project')
if (prep.error) return stop('prepare', prep.error)
const where = dirs(prep)
const planFile = input.plan || prep.plan_file
log(`Run ${prep.run_id} in ${prep.active_dir}`)

// ---------------------------------------------------------------- plan
const planVerdict = {
  type: 'object',
  required: ['error', 'status', 'findings'],
  properties: {
    error: errorField,
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    findings: { type: 'string', description: 'Each failed check, with the plan text it concerns.' },
  },
}
const planDone = {
  type: 'object',
  required: ['error', 'plan_file'],
  properties: { error: errorField, plan_file: { type: 'string' } },
}

if (!input.plan) {
  phase('Plan')
  const planned = await agent(
    `${RULES}

Act as MEOW's planner for this request:
<request>
${request}
</request>

1. \`meow native round "${planFile}" --reset ${where}\`
2. \`meow native checkpoint planning --run-id ${prep.run_id} ${where}\`
3. \`meow native prompt planner ${where}\` and follow its instructions and
   skills. Explore the code in ${prep.active_dir} first.
4. Write the plan to ${planFile}: numbered tasks and a \`## Sprint Contract\`
   with concrete, testable pass/fail criteria for every requirement.
5. Read it back, then \`meow native checkpoint planned --run-id ${prep.run_id} --plan "${planFile}" ${where}\`.`,
    { label: 'planner', schema: planDone },
  )
  if (!planned) return stop('plan', 'agent stopped')
  if (planned.error) return stop('plan', planned.error)

  // plan-check: at most two rounds, a fresh checker each time.
  phase('Plan check')
  for (let round = 1; round <= 2; round++) {
    const check = await agent(
      `${RULES}

Act as MEOW's plan checker (round ${round} of 2). Read ${planFile} and this request:
<request>
${request}
</request>
FAIL only for: a request requirement missing or weakened in the Sprint
Contract; a criterion that is not concretely testable; tasks whose file paths
overlap in a task graph; or verification described in prose instead of a
runnable check. Otherwise PASS. Do not edit any file.`,
      { label: `plan-checker ${round}`, schema: planVerdict },
    )
    if (!check) return stop('plan-check', 'agent stopped')
    if (check.error) return stop('plan-check', check.error)
    if (check.status === 'PASS') break
    if (round === 2) {
      log('Plan check still fails after revision; continuing as the default workflow does.')
      break
    }
    const revised = await agent(
      `${RULES}

Act as MEOW's planner. Revise ${planFile} to fix these plan-check findings
without dropping or widening scope, then
\`meow native checkpoint planned --run-id ${prep.run_id} --plan "${planFile}" ${where}\`.
<findings>
${check.findings}
</findings>`,
      { label: 'revise-plan', schema: planDone },
    )
    if (!revised) return stop('plan-check', 'agent stopped')
    if (revised.error) return stop('plan-check', revised.error)
  }
}

// ---------------------------------------------------------------- approve
// Workflows take no input mid-run; approval is a separate launch.
if (input.stopAfterPlan) {
  log(`Plan ready for approval: ${planFile}`)
  return {
    status: 'awaiting_approval',
    run_id: prep.run_id,
    plan_file: planFile,
    next: `Relaunch with args { request, plan: "${planFile}", name: "<same name>" } to implement it.`,
  }
}

// ---------------------------------------------------------------- rounds
const generated = {
  type: 'object',
  required: ['error', 'changed', 'reply'],
  properties: {
    error: errorField,
    changed: { type: 'boolean', description: 'Whether any source file was edited.' },
    reply: { type: 'string', description: 'A short account of what was done.' },
  },
}
const checked = {
  type: 'object',
  required: ['error', 'status', 'output'],
  properties: {
    error: errorField,
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    output: { type: 'string', description: 'Failing command output, trimmed to the relevant part.' },
  },
}
const briefing = {
  type: 'object',
  required: ['error', 'system_prompt', 'query'],
  properties: { error: errorField, system_prompt: { type: 'string' }, query: { type: 'string' } },
}
const verdict = {
  type: 'object',
  required: ['error', 'status', 'summary', 'review'],
  properties: {
    error: errorField,
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    summary: { type: 'string' },
    review: { type: 'string', description: 'The full review file text.' },
  },
}

let feedback = ''
let previousReview = ''
let lastFindings = ''
let passed = false
for (let round = 1; round <= prep.max_rounds; round++) {
  phase(`Round ${round}`)
  const counter = await agent(
    `${RULES}\n\nRun \`meow native round "${planFile}" ${where}\` and return its JSON status.`,
    {
      label: `round ${round}`,
      schema: {
        type: 'object',
        required: ['error', 'exhausted'],
        properties: { error: errorField, exhausted: { type: 'boolean' } },
      },
    },
  )
  if (!counter) return stop('rounds', 'agent stopped')
  if (counter.error) return stop('rounds', counter.error)
  if (counter.exhausted) break

  // generate
  const task = feedback
    ? `Fix the following, then stop. Verify each finding first; report unsupported or out-of-scope ones instead of acting on them.\n<feedback>\n${feedback}\n</feedback>`
    : `Implement the tasks in ${planFile}.`
  const gen = await agent(
    `${RULES}

Act as MEOW's generator in ${prep.active_dir}.
1. \`meow native checkpoint generator_started --run-id ${prep.run_id} --round ${round} ${where}\`
2. \`meow native prompt generator --plan "${planFile}" ${where}\`; follow its
   instructions, tools and skills. Stay within the plan's Sprint Contract.
3. ${task}
4. After every source edit run \`meow native lint --file <path> ${where}\` and
   fix what it reports. Do not commit or grade your own work.
5. \`meow native checkpoint generator_finished --run-id ${prep.run_id} --round ${round} ${where}\`
   (on an unexpected stop use \`interrupted_mutation\` instead).`,
    { label: `generator ${round}`, schema: generated },
  )
  if (!gen) return stop('generate', 'agent stopped')
  if (gen.error) return stop('generate', gen.error)

  // verify
  const checks = await agent(
    `${RULES}

Run MEOW's required checks in ${prep.active_dir}:
1. \`meow native lint ${where}\`; fix \`blocking\` findings (not informational ones).
2. \`meow native review-checks --background ${where}\`, then poll
   \`meow native review-checks --status ${where}\` until it finishes. Pending or
   interrupted checks are infrastructure: rerun them, they are not a FAIL.
Return PASS only when the current required lint/test/build evidence passed.`,
    { label: `verify ${round}`, schema: checked },
  )
  if (!checks) return stop('verify', 'agent stopped')
  if (checks.error) return stop('verify', checks.error)
  if (checks.status === 'FAIL') {
    feedback = `Required checks failed:\n${checks.output}`
    continue
  }

  // review: brief, then a fresh reviewer, then validate the file.
  const brief = await agent(
    `${RULES}

1. \`meow native checkpoint reviewer_started --run-id ${prep.run_id} --round ${round} ${where}\`
2. \`meow native prompt reviewer-plan --plan "${planFile}" ${prep.use_worktree ? '--worktree ' : ''}${where}\`
Return its system_prompt and query verbatim. Do not review anything yourself.`,
    { label: `brief reviewer ${round}`, schema: briefing },
  )
  if (!brief) return stop('review', 'agent stopped')
  if (brief.error) return stop('review', brief.error)

  const prior = previousReview
    ? `\n\nThis is a re-review. First resolve each prior FAIL as fixed, still failing or withdrawn.\n<prior_review>\n${previousReview}\n</prior_review>\n<fix_reply>\n${gen.reply}\n</fix_reply>`
    : ''
  const reviewed = await agent(
    `${brief.system_prompt}

${brief.query}

Work in ${prep.active_dir}. Write your report to ${prep.review_file}: it must
start with \`SUMMARY: ...\`, then \`STATUS: PASS\` or \`STATUS: FAIL\`, then
evidence for each criterion. Do not edit any other file.${prior}`,
    { label: `reviewer ${round}` },
  )
  if (reviewed === null) return stop('review', 'reviewer stopped')

  const result = await agent(
    `${RULES}

1. \`meow native verdict "${prep.review_file}" ${where}\`. Trust this result, not
   any chat summary; a missing or malformed report is FAIL.
2. \`meow native checkpoint reviewer_finished --run-id ${prep.run_id} --round ${round} --review "${prep.review_file}" --reviewer <PASS|FAIL> ${where}\`
Return the status, summary and the full text of ${prep.review_file}.`,
    { label: `verdict ${round}`, schema: verdict },
  )
  if (!result) return stop('review', 'agent stopped')
  if (result.error) return stop('review', result.error)
  log(`Round ${round}: ${result.status} - ${result.summary}`)
  if (result.status === 'PASS') {
    passed = true
    break
  }
  if (round > 1 && !gen.changed) {
    return stop('rounds', 'stalled: the fix changed no code and review still fails', { review_file: prep.review_file })
  }
  if (lastFindings && result.summary === lastFindings) {
    return stop('rounds', 'stalled: two rounds repeated the same findings', { review_file: prep.review_file })
  }
  lastFindings = result.summary
  previousReview = result.review
  feedback = `The reviewer found issues.\n${result.review}`
}

if (!passed) {
  return {
    status: 'exhausted',
    run_id: prep.run_id,
    review_file: prep.review_file,
    message: `Did not pass within ${prep.max_rounds} rounds; see the review file for the last feedback.`,
  }
}

// ---------------------------------------------------------------- checks + deliver
phase('Deliver')
const delivered = await agent(
  `${RULES}

Finish the run: \`meow native finalize ${prep.run_id} ${where}\`. It revalidates
evidence and final checks, then commits and pushes a separate worktree's
branch. Report its JSON. If \`complete\` is false, put its reason in error.
Then read \`meow status ${prep.run_id} --work-dir "${prep.project_dir}"\` for
the saved MR target. Do not open the MR yourself.`,
  {
    label: 'finalize',
    schema: {
      type: 'object',
      required: ['error', 'complete'],
      properties: {
        error: errorField,
        complete: { type: 'boolean' },
        branch: { type: 'string' },
        target: { type: 'string' },
      },
    },
  },
)
if (!delivered) return stop('deliver', 'agent stopped')
if (delivered.error || !delivered.complete) return stop('deliver', delivered.error || 'finalize did not complete')

return {
  status: 'complete',
  run_id: prep.run_id,
  plan_file: planFile,
  review_file: prep.review_file,
  branch: delivered.branch,
  target: delivered.target,
  next: 'Open the MR/PR into the target and report its pipeline status.',
}

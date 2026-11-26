// Runs a MEOW workflow script with scripted agents; prints labels and result.
// argv: script path, JSON args, JSON script {labelPrefix: [answers...]}.
import { readFileSync } from 'node:fs'
const [script, rawArgs, rawAnswers] = process.argv.slice(2)
const answers = JSON.parse(rawAnswers)
const calls = []
async function agent(prompt, options = {}) {
  const label = options.label || '?'
  calls.push(label)
  const key = Object.keys(answers)
    .filter(prefix => label.startsWith(prefix))
    .sort((a, b) => b.length - a.length)[0]
  if (key === undefined) return options.schema ? { error: '' } : 'done'
  const queue = answers[key]
  const answer = queue.length > 1 ? queue.shift() : queue[0]
  return answer === null ? null : answer
}
// Like Claude Code's runner, the script cannot see its own `meta`.
const src = readFileSync(script, 'utf8').replace(/^export const meta = .*$/m, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const run = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', src)
const result = await run(agent, null, null, () => {}, () => {}, JSON.parse(rawArgs))
console.log(JSON.stringify({ calls, result }))

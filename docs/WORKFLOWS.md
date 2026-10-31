# Workflows and agent definitions

A run executes a named TOML workflow; MEOW keeps preparation, worktree/setup,
onboarding, plan ownership, and terminal-state policy.

```bash
meow run "Add a dark mode toggle" --name dark-mode --workflow quick
meow run "Audit the API" --workflow review-and-fix --no-worktree --input topic=API
```

- `default`: plan → plan-check (≤2 rounds) → approval → tasks →
  loop{generate, verify, review} → checks → delivery.
- `quick`: short plan (`-m` pauses to approve), ≤3 rounds of generate → checks →
  review (after passing checks), final checks, delivery; no task graph or AI tester.
- `review-and-fix`: reviews existing code, fixes failures, final checks; no delivery.
  `--test` works without a plan, using the request as its basis.
- `test-fix`: ≤4 rounds of fix → verify → tester → review on existing code; no delivery.

Plan reviewers/testers also get the original request (even on resume) and verify
each criterion by observed behavior; ejected copies need equivalent prose.

`plan-check` (skipped for given plans/resume) gates the plan with read-only
`plan-checker`: FAIL only for lost requirements, untestable criteria, or graph
overlap/prose verification. Round 2 revises it (planner `revise`); a stop names
`<plan-stem>.plan-checker.stage.md` before any code exists.

Invalid task graphs get one `task_graph_repair` attempt, else run sequentially
(reason journaled). `max_parallel_tasks` (default 2) bounds task worktrees.

`--workflow NAME` overrides `[workflow].default` (otherwise `"default"`).
Repeated `--input name=value` supplies identifier-keyed strings (values may
contain `=` or be empty; last wins), persisted with journal redaction.
Workflow flags support Jira/background runs but reject lint-fix.
See [verification](VERIFICATION.md) for `auto_test`, `short_plan`, and test selection.

## Resolution and editing

Whole files resolve by name, highest priority first:

| Scope | Agent directory | Workflow directory |
|---|---|---|
| local | `.meow/local/agents/` | `.meow/local/workflows/` |
| project | `.meow/agents/` | `.meow/workflows/` |
| user | `~/.meow/agents/` | `~/.meow/workflows/` |
| builtin | shipped `project/custom/builtin/agents/` | shipped `project/custom/builtin/workflows/` |

Conventional folders need no config. Extra `[custom]` sources follow
[CUSTOM.md](CUSTOM.md); workflow sources cannot restrict roles.
Same-scope duplicates fail; overrides are reported. Resolution uses the run's
config root even in task worktrees.

`meow agents` supports list/show/eject/diff; `meow workflows` supports
list/show/validate/eject. All accept `--work-dir PATH`. Eject copies the shipped
definition even when another scope overrides it, defaults to project scope,
accepts local/user, and refuses overwrite. Copies record
`ejected_from = "builtin:name@<sha256>"`; run startup and show/validate warn when
that origin changes or disappears, with paths for inspecting the copy. Workflow
diagnostics include called workflows and agents, and flag ejected agents the
selected workflow does not use. Conditional steps, delegates, task workers, and
delivery fixers count as possible uses. Valid copies continue unchanged across
upgrades; missing references fail before agents start. Handwritten definitions
have no drift/unused warning.

## Agents

Create `<name>.toml`: lowercase letters/digits separated by single hyphens, at
most 64 characters; optional `name` must match. `description` and `prompt` are required.

```toml
description = "Review API authorization"
model = "inherit"
tools = ["Read", "Grep", "Glob", "Write"]
skills = []
agents = []
session = "one-shot"
lint_on_edit = false
prompt = '''
Review {{inputs.topic}} in {{active_dir}}.
Write findings to {{output_file}}.
{{verdict_format}}
'''
```

Hyphens map to underscores for roles. Model order: `[models].role`, agent,
role/reviewer fallback; `inherit` supplies none (explorer uses haiku). Default
and role skill lists combine with definition and discovered skills.

`session = "persistent"` retains a role session inside a loop until it ends,
rendering current values each turn; outside loops invocations own sessions.
`lint_on_edit` enables post-edit lint. `[variants]` select alternate prompts
(missing variants use main); `[fragments]` defines reusable text.

Named `agents` delegates are capped to parent tools and exclude Agent.
Configured custom agents retain planner/generator (or explicit role) compatibility;
conventional definitions run only when named. Explorer is overridable. Claude Code
Markdown agents are a frontmatter/body import format; specify tools for stages.

## Templates and expressions

Syntax: `{{value}}`, `{{> fragment}}`, nested
`{{#if expression}}...{{else}}...{{/if}}`, and `{{#unless expression}}...{{/unless}}`.
Unknown variables/fragments fail before agents, naming the definition file;
unavailable values or missing dotted keys on known values are empty.
Substituted Windows paths retain spaces/backslashes; TOML newlines survive and
paragraph folding preserves substituted values, lists, and code.

Conditions allow dotted identifiers, quoted strings, integers, booleans,
`== != < <= > >=`, `and/or/not`, and parentheses; no calls.

| Values | Meaning |
|---|---|
| `plan_given`, `resume_review`, `review_first`, `parallel`, `test`, `bug_mode`, `checks_configured` | Run flags; review_first includes resume-review/parallel tasks |
| `round`, `feedback`, `steps.<id>.status`, `inputs.<name>` | Loop state, failed-gate feedback, PASS/FAIL/done/skipped results, input strings |
| `active_dir`, `docs_dir`, `request`, `feature_name`, `check_worktree_hygiene`, `write_tests` | Run context |
| `plan_file`, `review_file`, `report_file`, `output_file` | Invocation artifact paths |
| `lint_instructions`, `focus` | Computed lint/review context |
| `review_basis`, `has_diff`, `request_label`, `target`, `branch`, `source_sha`, `target_sha`, `merge_base` | Review scope |
| `baseline`, `head` | Docs-update revisions |
| `objective`, `task_drafts`, `max_tasks`, `conflicted_paths` | Orchestrator objective, fixed Jira drafts, task cap, conflict paths |
| `verdict_format`, `tasks_format`, `decision_format` | Code-owned gate/task/triage contracts |

`when` receives flags, round, steps, inputs; task/prompt templates also receive
other values. Gates always require a readable artifact starting `SUMMARY:`,
then `STATUS: PASS` or `STATUS: FAIL`; missing/malformed/unreadable/timed-out
verdicts fail. MEOW appends the contract if omitted. Ordinary stages may return text.

## Workflow steps

`description` is required; `steps = []` is valid. Each step selects one kind:

| Kind | Action |
|---|---|
| `uses = "builtin.name"` | MEOW operation |
| `agent = "name"` | Resolved role/report |
| `gate = "name"` | Role with parsed verdict |
| `loop = [steps]` | Repeat until blocking gates pass in one round |
| `parallel = [steps]` | Concurrent branches, wait for all |
| `call = "workflow"` | Compose another workflow |

Steps accept `id`, `when`, positive timeout seconds; agents/gates also accept
a task template. IDs use lowercase letters/digits/hyphens/underscores, must be
unique after expansion, and are required for agent/gate/loop/parallel.
Builtin/call IDs default to the target. Loops use positive `max_rounds` or
the run limit and stop early on a stall ([runs](RUNS.md#verification-and-delivery)).
Calls preserve condition/timeout as resolved sequence groups; cycles or
duplicate expanded IDs fail.

```toml
description = "Review API security, then check"
[[steps]]
id = "security-rounds"
max_rounds = 3
loop = [
  { id = "fix", uses = "builtin.generate", when = "round > 1" },
  { id = "security", gate = "security-reviewer", task = "Review {{inputs.topic}}. {{feedback}}" },
]
[[steps]]
uses = "builtin.checks"
```

Gate `blocks` defaults to loop inside loops, delivery outside:

| Value | FAIL behavior |
|---|---|
| `advise` | Record only |
| `loop` | Fail round and feed back; outside loops stop run |
| `delivery` | Block checks/delivery and fail run |
| `run` | Stop immediately |

Loops require a blocking gate or builtin.review. Parallel branches must be
agent/gate definitions without Edit/Bash/NotebookEdit and share the worktree;
report paths stay distinct by step ID. Tools/prompts never bypass permission policy.

## Built-in operations and recovery

`plan` writes/claims the plan (revises it in a loop round > 1 with feedback);
`approve_plan` invokes manual approval; `tasks` integrates task worktrees;
`generate` implements/fixes; `review` reviews/optionally tests; `test` always
tests (not under `--force-disable-tests`; `review` then skips its own tester);
`verify` runs the checks inside a loop and fails the round with their output;
`checks` records current lint/test/build/browser evidence, reusing `verify`
results only when source/config are unchanged (resume reruns). `deliver`
delivers. Checks need reviewer PASS only if builtin.review exists; delivery
gates must pass before checks/delivery.

Builtins retain phase names. Custom stages journal stage_started/stage_finished
with status/summary/file/round under results.stages. Reports are
`<plan-stem>.<id>.stage.md`, otherwise `<docs_dir>/<id>.stage.md`; latest-plan
lookup excludes them. All steps honor cancellation, accounting, and timeout.
Only SDK crash recovery and explicit loops retry. Custom steps run once per run.

Status shows workflow and stages. Journals retain fingerprints, completed
top-level IDs, and the active loop/step. Resume rejects changed definitions,
skips completed top-level steps, and enters interrupted loops review-first;
evidence freshness/delivery guards still apply. Role session IDs stay Claude's.

`meow native workflow NAME` inspects resolved JSON; `native prompt ROLE`
renders resolved instructions/models. Native helpers do not drive custom
workflow trees; `/meow:run` uses CLI for named workflows.

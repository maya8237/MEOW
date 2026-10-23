# Workflows and agent definitions

A run executes a named TOML workflow. MEOW retains preparation, worktree/setup,
onboarding, plan ownership, and terminal-state policy.

```bash
meow run "Add a dark mode toggle" --name dark-mode --workflow quick
meow run "Audit the API" --workflow review-and-fix --no-worktree --input topic=API
```

- `default`: knowledge → shaping → plan/approval → task graph → generation/review → checks → delivery.
- `quick`: short plan (`-m` pauses to approve), then ≤3 rounds of generate → checks → review
  (review waits for passing checks), final checks, delivery. No knowledge/shaping/task
  graph; configured tests don't enable the AI tester.
- `review-and-fix`: reviews existing code first, fixes later failures, checks, no automatic delivery.
  `--test` works without a plan, using the request as its basis.

Plan-based reviewers/testers also receive the original request, including on
resume. They must verify every criterion from observed behavior; passing tests
alone cannot excuse requirements lost/weakened in a plan. Ejected definitions
replace this prose and need equivalent instructions.

Planner task graphs are validated for dependencies/file ownership. Invalid
graphs get one planner repair using `task_graph_repair` (custom fragment or
shipped default), preserving plan/contract. Failed repair falls back to
sequential generation and journals the reason; absent graphs need no repair.

`--workflow NAME` overrides `[workflow].default` (otherwise `"default"`).
Repeated `--input name=value` supplies identifier-keyed strings; values may
contain `=` or be empty, and last wins. Inputs persist with journal redaction.
Workflow flags support Jira/background runs but reject lint-fix.
Top-level `auto_test = false`: only `--test` enables the AI tester, not configured
`[[tester.tests]]`. `short_plan = true` asks the planner for a brief plan.

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
config root even when tasks execute in other worktrees.

`meow agents` supports list/show/eject/diff; `meow workflows` supports
list/show/validate/eject. All accept `--work-dir PATH`. Eject copies the shipped
definition even when another scope overrides it, defaults to project scope,
accepts local/user, and refuses overwrite. Copies record
`ejected_from = "builtin:name@<sha256>"`; run startup and show/validate warn when
that origin changes or disappears, with paths for inspecting the copy. Workflow
diagnostics include called workflows and agents, and identify ejected agents the
selected workflow does not use (another workflow may use them). Conditional steps,
delegates, task workers, and delivery fixers count as possible uses. Valid copies
continue unchanged across upgrades; missing references fail before agents start.
Handwritten definitions without origin metadata have no drift/unused warning.

## Agents

Create `<name>.toml`: lowercase letters/digits separated by single hyphens,
at most 64 characters. Optional `name` must match the stem.
`description` and `prompt` are required.

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

Hyphens map to underscores for config/permission/session roles.
Model order: explicit `[models].role`, agent model, role/reviewer fallback.
`inherit` supplies no explicit model; built-in explorer uses haiku.
Installed default/role skill lists combine with definition and discovered skills.

`session = "persistent"` lazily retains a role session inside a loop and closes
it at loop end, rendering current values each turn. Outside loops, invocations
own sessions. `lint_on_edit` enables post-edit lint. Optional `[variants]`
select alternate prompts (missing variants use main); `[fragments]` defines reusable text.

Named `agents` delegates are capped to parent tools and exclude Agent.
Configured custom agents retain planner/generator (or explicit role) compatibility;
conventional definitions run only when named. Explorer is overridable.
Claude Code Markdown agents remain a frontmatter/body import format; specify
tools for standalone stages.

## Templates and expressions

Syntax: `{{value}}`, `{{> fragment}}`, nested
`{{#if expression}}...{{else}}...{{/if}}`, and `{{#unless expression}}...{{/unless}}`.
Unknown variables/fragments fail before agents, naming the definition file;
unavailable values or missing dotted keys on known values are empty.
Substituted Windows paths retain spaces/backslashes. TOML newlines survive;
paragraph folding preserves substituted values, lists, and code.

Conditions allow dotted identifiers, quoted strings, integers, booleans,
`== != < <= > >=`, `and/or/not`, and parentheses; no calls or Python evaluation.

| Values | Meaning |
|---|---|
| `plan_given`, `resume_review`, `review_first`, `parallel`, `test`, `bug_mode`, `checks_configured` | Run flags; review_first includes resume-review/parallel tasks |
| `round`, `feedback`, `steps.<id>.status`, `inputs.<name>` | Loop state, failed-gate feedback, PASS/FAIL/done/skipped results, input strings |
| `active_dir`, `docs_dir`, `request`, `feature_name`, `check_worktree_hygiene`, `write_tests` | Run context |
| `plan_file`, `review_file`, `report_file`, `output_file` | Invocation artifact paths |
| `shape`, `preplan`, `lint_instructions`, `focus` | Computed shaping/lint/review context |
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
the run limit. Calls preserve condition/timeout as resolved sequence groups;
cycles or duplicate expanded IDs fail.

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
report paths remain distinct by step ID. Tools/prompts never bypass permission
policy; discovered agent names are valid roles, unknown names fail config.

## Built-in operations and recovery

`builtin.knowledge` gathers evidence; `shape` resolves requirements/breadboards;
`plan` writes/claims the plan; `approve_plan` invokes manual approval;
`tasks` integrates task worktrees; `generate` implements/fixes;
`review` reviews/optionally tests; `verify` runs the checks inside a loop and
fails the round with their output; `checks` records current lint/test/build/browser
evidence, reusing `verify` results when no existing source changed since; `deliver` performs verified delivery. Include checks when completion
needs deterministic verification. Reviewer PASS is required by checks only if
builtin.review exists. Delivery gates must pass before checks/delivery.

Builtins retain phase names. Custom stages journal stage_started/stage_finished
with status/summary/file/round under results.stages. Reports are
`<plan-stem>.<id>.stage.md`, otherwise `<docs_dir>/<id>.stage.md`;
latest-plan lookup excludes them. All steps honor cancellation, accounting,
and timeout. Only SDK crash recovery and explicit loops retry. Custom workflow
steps run once per run, not once per task.

Status shows workflow scope/name and stages. Journals retain the resolved
fingerprint (including called workflows/roles/delegates), completed top-level
IDs, and active loop/step. Resume reloads that workflow, rejects changed
definitions, skips completed top-level steps, and enters interrupted loops
review-first. Evidence freshness/delivery guards still apply.
Legacy journals select default regardless of current config; role session IDs
remain transcript references owned by Claude.

`meow native workflow NAME` inspects resolved JSON; `native prompt ROLE`
renders resolved instructions/models. Native helpers do not drive custom
workflow trees; `/meow:run` uses CLI for named workflows.

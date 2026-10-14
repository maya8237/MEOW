# Workflows and agent definitions

A run executes a named TOML workflow. Every step is optional: a workflow can
plan, inspect existing code, run a custom gate, or combine those operations.
Run preparation, worktrees, setup, onboarding, plan ownership, and the final
run status remain owned by MEOW.

```bash
meow workflows list
meow workflows show quick
meow run "Add CSV export" --name csv-export --workflow quick
meow run "Audit the API" --workflow review-only --no-worktree --input topic=API
```

The shipped `default` workflow gathers knowledge, shapes the request, plans,
approves, runs a task graph when present, loops generation and review, checks,
and delivers. `quick` omits knowledge and shaping. `review-only` reviews existing
code first, fixes failures in later rounds, and runs checks; it has no automatic
delivery. `--test` also works without a plan in that workflow: the tester uses
the review request as its basis. During a run (including resume), plan-based
review and testing also receive the original request. The builtin roles must
check each acceptance criterion against observed behavior and fail for
requirements omitted or weakened by the plan; a passing existing suite alone
is insufficient evidence. Scoped agent overrides replace the builtin prose,
so ejected reviewer/tester definitions need equivalent instructions.

Before executing a planner task graph, MEOW validates its dependencies and
file ownership. If the graph is invalid (for example, independent tasks own
overlapping files), MEOW sends the validation error to the configured planner
for one repair attempt and validates the revised graph. Repair changes only
the graph, preserving the plan and Sprint Contract. If repair fails or the
graph remains invalid, MEOW records the reason in the run journal and uses
the normal sequential generator flow. Plans without a task graph need no
repair. Planner definitions may customize repair instructions with the
`task_graph_repair` fragment; otherwise the shipped instructions apply.

Without `--workflow`, config chooses the workflow:

```toml
[workflow]
default = "quick"
```

Repeated `--input name=value` flags supply strings. Names are identifiers;
values may contain `=` or be empty. The last value for a repeated name wins.
These inputs are available as `inputs.name` and retained in the run journal,
subject to its existing secret redaction. Workflow options also pass through
Jira and background feature runs. They do not apply to `--lint-fix`.

## Resolution and editing

Definitions resolve by file name, with whole-file replacement:

| Priority | Scope | Agent directory | Workflow directory |
|---|---|---|---|
| 1 | local | `.meow/local/agents/` | `.meow/local/workflows/` |
| 2 | project | `.meow/agents/` | `.meow/workflows/` |
| 3 | user | `~/.meow/agents/` | `~/.meow/workflows/` |
| 4 | builtin | shipped package's `project/custom/builtin/agents/` | shipped package's `project/custom/builtin/workflows/` |

Conventional directories need no configuration. `[custom].agents`,
`[custom].skills`, and `[custom].workflows` may also list extra directories,
using the same scope/path rules documented in [INTEGRATIONS.md](INTEGRATIONS.md#custom-skills-and-agents).
Workflow sources do not take `roles`. A duplicate name within one scope is an
error; higher-scope overrides are reported. Definitions resolve from the run's
config root, including when tasks execute in other worktrees.

```bash
meow agents list
meow agents show reviewer
meow agents eject reviewer --scope project
meow agents diff reviewer
meow workflows eject quick --scope local
meow workflows validate
meow workflows validate quick
```

All commands accept `--work-dir PATH`. Eject copies the shipped definition,
even if a higher scope already overrides it, and refuses to overwrite a file
in the chosen scope. Default scope is `project`; `local` and `user` are also
available. The copy records `ejected_from = "builtin:name@<sha256>"`.
`show` and workflow `validate` warn about drift when the shipped origin changes.
Compare the files and update your copy deliberately; upgrades do not merge it.

## Agents

Create `<name>.toml`, with a name of lowercase letters/digits separated by
single hyphens, at most 64 characters. An optional `name` field must match the
file stem. `description` and `prompt` are required.

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

[variants]
branch = '''Review the branch {{branch}} against {{target}}.'''

[fragments]
scope = '''Stay within the requested API changes.'''
```

The agent name maps to its config/permission/session role with hyphens changed
to underscores: `security-reviewer` uses `security_reviewer`. Model precedence
is an explicit `[models].role`, the agent's model, then the role/reviewer config
fallback. `inherit` has no explicit agent model. The built-in explorer supplies
`haiku`. Installed `[agent_skills]` defaults and role lists combine with the
definition's skills and discovered custom skills.

`session = "persistent"` retains one lazily opened session per role within an
enclosing loop, closed at loop end. Prompts are rendered with current step
values on later turns. Outside a loop an invocation owns its session.
`lint_on_edit` enables the project's post-edit lint hook. Variants are optional
alternate prompts; an absent variant uses the main prompt.

An `agents` list names resolved delegates. Their tools are capped to the
parent's tools and exclude `Agent`. Agents registered through `[custom].agents`
retain compatibility: they are offered to planner/generator or their configured
roles. Agents found only in conventional directories run when explicitly named
by a workflow or another agent. `explorer` can be overridden like any role.
Claude Code `.md` agent files remain an import format with frontmatter and a
prompt body; specify tools when using one as a standalone workflow stage.

## Templates and expressions

Use `{{value}}`, `{{> fragment}}`, `{{#if expression}}...{{else}}...{{/if}}`,
and `{{#unless expression}}...{{/unless}}`. Blocks nest. Missing values are
empty, but unknown variables and fragments are errors naming the definition
file before an agent starts. A missing dotted key on a known value is empty.
Substituted Windows paths retain backslashes and spaces verbatim.

Conditions support dotted identifiers, quoted strings, integers, `true`/`false`,
comparisons (`== != < <= > >=`), `and`, `or`, `not`, and parentheses. There
are no function calls or Python evaluation. TOML line breaks are retained in
role prompts; paragraph folding normalizes prose without changing substituted
values or list/code structure.

| Available values | Meaning |
|---|---|
| `plan_given`, `resume_review`, `review_first`, `parallel`, `test`, `bug_mode`, `unattended`, `checks_configured` | Run flags; `review_first` is resume-review or a parallel task run |
| `round`, `feedback`, `steps.<id>.status`, `inputs.<name>` | Loop round/failed gate feedback, step outcomes (`PASS`, `FAIL`, `done`, `skipped`), supplied strings |
| `active_dir`, `docs_dir`, `request`, `feature_name`, `check_worktree_hygiene` | Project/run context |
| `plan_file`, `review_file`, `report_file`, `output_file` | Paths appropriate to the invocation |
| `shape`, `preplan`, `lint_instructions`, `focus` | Computed shaping, lint, and review context |
| `review_basis`, `has_diff`, `request_label`, `target`, `branch`, `source_sha`, `target_sha`, `merge_base` | Review context |
| `baseline`, `head` | Documentation-update revisions |
| `objective`, `task_drafts`, `max_tasks`, `conflicted_paths` | Orchestrator context: the objective text, the fixed Jira task drafts, the task cap, the files a merge left conflicted |
| `verdict_format`, `tasks_format`, `decision_format` | Code-owned contracts: the gate verdict, the orchestrator task graph, the triage decision |

Values unavailable to an invocation are empty. `when` uses the run flags,
`round`, `steps`, and `inputs`; task/prompt templates also receive the other
documented values. MEOW appends the verdict contract when a gate prompt omits
it. A readable artifact with `SUMMARY:` followed by `STATUS: PASS` or
`STATUS: FAIL` is required; absent, unreadable, malformed, or timed-out
verdicts count as FAIL. An ordinary agent stage may return its report as text.

## Workflow steps

`description` is required; `steps = []` is a valid empty workflow. A step sets
exactly one of the following:

| Kind | Purpose |
|---|---|
| `uses = "builtin.name"` | Run a MEOW operation |
| `agent = "name"` | Run a resolved agent and record its report |
| `gate = "name"` | Run an agent and parse its verdict |
| `loop = [steps]` | Repeat until all blocking gates pass in one round |
| `parallel = [steps]` | Run branches concurrently and wait for all |
| `call = "workflow"` | Compose another workflow |

Every step can set `id`, `when`, and positive `timeout` seconds. Agents and
gates may set a `task` template. IDs are unique across the expanded workflow;
agent/gate/loop/parallel IDs are required. Builtins/calls default to their target
name. IDs use lowercase letters/digits with hyphens or underscores.
Loops accept positive `max_rounds`, otherwise using the run's configured limit.
Calls preserve their own condition/timeout as an internal sequence group in
resolved JSON. Call cycles and duplicate expanded IDs are errors.

```toml
description = "Review API security, then run project checks"
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

Gate `blocks` is `loop` by default inside a loop and `delivery` outside:

| Value | FAIL behavior |
|---|---|
| `advise` | Record only |
| `loop` | Fail the round and supply feedback to the next one; outside a loop, stop the run |
| `delivery` | Prevent checks/delivery and end the run failed |
| `run` | Stop immediately |

Each loop must contain a blocking gate or `builtin.review`. Parallel branches
must be agent/gate steps whose definitions lack `Edit`, `Bash`, and
`NotebookEdit`; branches share the active worktree. Gate report paths remain
distinct by step ID. Changing tools, prompts, or permissions never bypasses
the role's permission policy. Permission role names can include any discovered
agent; unknown roles fail configuration validation.

## Built-in operations

`builtin.knowledge` gathers repository evidence; `builtin.shape` resolves
requirements and optional breadboards; `builtin.plan` writes and claims the
plan; `builtin.approve_plan` invokes configured manual approval;
`builtin.tasks` integrates a parallel task graph; `builtin.generate` implements
or fixes; `builtin.review` reviews and optionally tests; `builtin.checks` records
current required lint/test/build/browser evidence; `builtin.deliver` performs
verified delivery. Include `builtin.checks` when a workflow needs deterministic
verification before completion or delivery.

Checks require a reviewer verdict only when the workflow contains
`builtin.review`. Delivery gates must pass before checks or delivery. Built-in
steps retain existing phase names. A custom step records `stage_started` and
`stage_finished`, with status, summary, file, and round under `results.stages`.
Reports are `<plan-stem>.<id>.stage.md` beside a known plan, otherwise
`<docs_dir>/<id>.stage.md`; these reports are excluded from latest-plan lookup.

Every step checks cancellation, runs within usage accounting and cancellation
handling, and honors its timeout. There are no automatic retries beyond SDK
crash handling and explicitly configured loop rounds. `builtin.tasks` retains
the existing task-worktree execution; custom workflow steps run once per run,
not once per parallel task.

## Inspecting and resuming

`meow status` shows `Workflow: name (scope)` and each custom stage's status.
`results.workflow` retains the resolved name, scope, fingerprint, and completed
top-level IDs, with active-step/loop markers used for interruption recovery.
The fingerprint includes called workflows, used agents, and their delegates.
`meow resume RUN_ID --continue` reloads the same workflow, refuses changed
definitions, skips completed top-level steps, and enters an interrupted loop
review-first. Saved gates and check results remain subject to delivery guards
and evidence freshness. Legacy journals without workflow metadata use `default`
and the existing resume behavior. Session references remain keyed by role;
Claude owns the transcripts.

`meow native workflow NAME` prints resolved JSON; `meow native prompt ROLE`
renders the same resolved agent definitions and effective models as the SDK.
Native helpers expose definitions for inspection/planning/review; they do not
drive a custom workflow tree. `/meow:run` uses the CLI for named workflows.

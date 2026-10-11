"""Agent and verdict-gate steps; artifact names are owned by plan_files."""

from meow.agents.reviewer import _verdict_status
from meow.agents.stage import StageAgent
from meow.project.custom.templates import render
from meow.project.plan_files import plan_stage_file, stage_file
from meow.project.prompts import lint_instructions, shape_values, verdict_format


def record_stage(state, step, status, summary, output):  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    result = {
        "status": status,
        "summary": summary,
        "file": str(output) if output else None,
        "round": state.round,
    }
    state.statuses[step.id] = result
    saved = state.store.load(state.run_id).results.get("stages", {})
    state.journal("stage_finished", results={"stages": {**saved, step.id: result}})


def stage_values(state, step):
    active = state.sprint.active_working_dir()
    docs = active / state.sprint.config["docs_dir"]
    output = (
        plan_stage_file(state.plan_file, step.id)
        if state.plan_file
        else stage_file(docs, step.id)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    return {
        **state.variables(),
        "active_dir": active,
        "docs_dir": docs,
        "request": state.request,
        "feature_name": state.feature_name,
        "plan_file": state.plan_file,
        "review_file": output,
        "report_file": output,
        "output_file": output,
        "shape": shape_values(state.sprint.config.get("_shape_context")),
        "preplan": state.sprint.config.get("_preplan_context"),
        "lint_instructions": lint_instructions(state.sprint.config["lint"]),
        "verdict_format": verdict_format(output, "finding")
        if step.kind == "gate"
        else None,
    }


async def run_stage(state, step):
    agent = state.sprint.config["customizations"].agent(step.target)
    values = stage_values(state, step)
    output = values["output_file"]
    output.unlink(missing_ok=True)
    state.journal("stage_started", round=state.round)
    task = render(
        step.task
        or "Perform this stage for {{request}}. Write your report to {{output_file}}.",
        values,
        where=str(agent.file),
    )
    response = await StageAgent(state.sprint).run(agent, task, values, state.pool)
    try:
        text = output.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = (
            "SUMMARY: Stage did not produce a readable verdict.\nSTATUS: FAIL"
            if step.kind == "gate"
            else response or ""
        )
    status = _verdict_status(text) if step.kind == "gate" else "done"
    summary = next(
        (
            line.removeprefix("SUMMARY:").strip()
            for line in text.splitlines()
            if line.startswith("SUMMARY:")
        ),
        text[:1000],
    )
    record_stage(state, step, status, summary, output)
    if step.kind == "gate":
        state.gate_result(step, status, text)

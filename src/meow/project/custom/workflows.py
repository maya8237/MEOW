"""Strict TOML workflow definitions, composition, and preflight validation."""

import hashlib
import math
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from meow.project.custom.expressions import names
from meow.project.custom.rendering import PROMPT_VARIABLES, unknown_variables
from meow.project.custom.sources import CustomSource
from meow.project.custom.templates import template_names

if TYPE_CHECKING:
    from meow.project.custom.definitions import Customizations

BUILTIN_STEPS = frozenset(
    f"builtin.{name}"
    for name in (
        "knowledge",
        "shape",
        "plan",
        "approve_plan",
        "tasks",
        "generate",
        "review",
        "checks",
        "deliver",
    )
)
RUN_VARIABLES = frozenset({
    "plan_given",
    "resume_review",
    "review_first",
    "parallel",
    "test",
    "bug_mode",
    "unattended",
    "round",
    "checks_configured",
    "steps",
    "inputs",
})
_KINDS = frozenset({"uses", "agent", "gate", "loop", "parallel", "call"})
_BLOCKS = frozenset({"advise", "loop", "delivery", "run"})
_BUILTIN_AGENTS = {
    "builtin.plan": ("planner",),
    "builtin.generate": ("generator", "review-fixer"),
    "builtin.review": ("reviewer", "tester"),
}


@dataclass(frozen=True)
class Step:
    kind: str
    id: str
    when: str | None = None
    timeout: float | None = None
    target: str | None = None
    task: str | None = None
    blocks: str | None = None
    body: tuple["Step", ...] = ()
    max_rounds: int | None = None


@dataclass(frozen=True)
class WorkflowFile:
    name: str
    description: str
    steps: tuple[Step, ...]
    file: Path
    source: CustomSource
    ejected_from: str | None = None


def _positive(value, field: str, path: Path, *, integer=False):
    if value is None:
        return None
    valid = isinstance(value, int) if integer else isinstance(value, (int, float))
    if isinstance(value, bool) or not valid or value <= 0 or not math.isfinite(value):
        raise ValueError(f"{path}: {field} must be positive")
    return value


def _text(value, field: str, path: Path, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {field} must be non-empty text")
    return value


def _step(data: object, path: Path, inside_loop: bool) -> Step:  # ruff: ignore[too-many-statements, too-many-branches, complex-structure] -- strict validation of independent step fields
    if not isinstance(data, dict):
        raise ValueError(f"{path}: steps must be tables")
    kinds = set(data) & _KINDS
    if len(kinds) != 1:
        raise ValueError(f"{path}: step must have exactly one kind")
    kind = next(iter(kinds))
    allowed = {"id", "when", "timeout", kind}
    if kind in {"agent", "gate"}:
        allowed.add("task")
    if kind == "gate":
        allowed.add("blocks")
    if kind == "loop":
        allowed.add("max_rounds")
    if set(data) - allowed:
        raise ValueError(f"{path}: unsupported step keys {sorted(set(data) - allowed)}")
    target = (
        None
        if kind in {"loop", "parallel"}
        else _text(data[kind], kind, path, required=True)
    )
    step_id = _text(
        data.get("id"),
        "id",
        path,
        required=kind in {"agent", "gate", "loop", "parallel"},
    )
    step_id = step_id or target.removeprefix("builtin.")
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", step_id):
        raise ValueError(f"{path}: invalid step id {step_id!r}")
    blocks = (
        data.get("blocks", "loop" if inside_loop else "delivery")
        if kind == "gate"
        else None
    )
    if blocks is not None and (not isinstance(blocks, str) or blocks not in _BLOCKS):
        raise ValueError(f"{path}: invalid gate blocks {blocks!r}")
    body = (
        _steps(data[kind], path, inside_loop or kind == "loop")
        if kind in {"loop", "parallel"}
        else ()
    )
    return Step(
        kind=kind,
        id=step_id,
        when=_text(data.get("when"), "when", path),
        timeout=_positive(data.get("timeout"), "timeout", path),
        target=target,
        task=_text(data.get("task"), "task", path),
        blocks=blocks,
        body=body,
        max_rounds=_positive(data.get("max_rounds"), "max_rounds", path, integer=True),
    )


def _steps(data: object, path: Path, inside_loop=False) -> tuple[Step, ...]:
    if not isinstance(data, list):
        raise ValueError(f"{path}: steps must be an array")
    return tuple(_step(item, path, inside_loop) for item in data)


def load_workflow(path: Path, source: CustomSource) -> WorkflowFile:
    from meow.project.custom.definitions import _name

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if set(data) - {"name", "description", "steps", "ejected_from"}:
        raise ValueError(f"{path}: unsupported workflow keys")
    name = _name(data.get("name", path.stem), path)
    if name != path.stem:
        raise ValueError(f"{path}: name must match the file name")
    return WorkflowFile(
        name,
        _text(data.get("description"), "description", path, required=True),
        _steps(data.get("steps", []), path),
        path,
        source,
        _text(data.get("ejected_from"), "ejected_from", path),
    )


def walk(steps: tuple[Step, ...]):
    for step in steps:
        yield step
        yield from walk(step.body)


def _expand(steps, catalog, stack):
    output = []
    for step in steps:
        if step.kind == "call":
            if step.target in stack:
                raise ValueError(
                    f"workflow call cycle: {' -> '.join((*stack, step.target))}"
                )
            child = catalog.workflow(step.target)
            body = _expand(child.steps, catalog, (*stack, child.name))
            output.append(replace(step, kind="sequence", body=body))
        else:
            output.append(replace(step, body=_expand(step.body, catalog, stack)))
    return tuple(output)


def expand(workflow: WorkflowFile, catalog: "Customizations") -> tuple[Step, ...]:
    return _expand(workflow.steps, catalog, (workflow.name,))


def _has_gate(step: Step) -> bool:
    return any(
        (item.kind == "gate" and item.blocks != "advise")
        or (item.kind == "uses" and item.target == "builtin.review")
        for item in walk(step.body)
    )


def _variables(step, path):
    problems = []
    for field, text in (("when", step.when), ("task", step.task)):
        if text is None:
            continue
        try:
            found = names(text) if field == "when" else template_names(text)
            known = (
                RUN_VARIABLES if field == "when" else RUN_VARIABLES | PROMPT_VARIABLES
            )
            problems.extend(
                f"{path}: {field}: unknown variable {name!r}"
                for name in found
                if name.split(".")[0] not in known
            )
        except ValueError as exc:
            problems.append(f"{path}: {field}: {exc}")
    return problems


def _validate_step(step, catalog, path):
    problems = _variables(step, path)
    if step.kind == "uses" and step.target not in BUILTIN_STEPS:
        problems.append(f"{path}: unknown builtin {step.target!r}")
    if step.kind in {"agent", "gate"}:
        try:
            problems.extend(unknown_variables(catalog.agent(step.target)))
        except (KeyError, ValueError) as exc:
            problems.append(f"{path}: {exc}")
    if step.kind == "loop" and not _has_gate(step):
        problems.append(f"{path}: loop {step.id!r} needs a blocking gate")
    if step.kind == "parallel":
        problems.extend(_parallel_problems(step, catalog, path))
    return problems


def _parallel_problems(step, catalog, path):
    problems = []
    for branch in step.body:
        if branch.kind not in {"agent", "gate"}:
            problems.append(f"{path}: parallel branches must be agent/gate steps")
            continue
        try:
            agent = catalog.agent(branch.target)
        except KeyError:
            continue
        if agent.tools is None or set(agent.tools) & {"Edit", "Bash", "NotebookEdit"}:
            problems.append(f"{path}: parallel agent {agent.name!r} has unsafe tools")
    return problems


def validate(workflow: WorkflowFile, catalog: "Customizations") -> list[str]:
    try:
        steps = expand(workflow, catalog)
    except (KeyError, ValueError) as exc:
        return [f"{workflow.file}: {exc}"]
    seen = set()
    problems = []
    for step in walk(steps):
        if step.id in seen:
            problems.append(f"{workflow.file}: duplicate step id {step.id!r}")
        seen.add(step.id)
        problems.extend(_validate_step(step, catalog, workflow.file))
    return problems


def _normalized(path) -> bytes:
    """File bytes with CRLF folded to LF, so checkout line endings do not matter."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def fingerprint(workflow: WorkflowFile, catalog: "Customizations") -> str:  # ruff: ignore[complex-structure] -- recursively visit both workflow and agent dependencies once
    files = {}

    def agent_file(name):
        if ("agent", name) in files:
            return
        agent = catalog.agent(name)
        files["agent", name] = _normalized(agent.file)
        for delegate in agent.agents:
            agent_file(delegate)
        for delegate in catalog.agents_for(agent.role):
            agent_file(delegate.name)

    def workflow_file(item):
        if ("workflow", item.name) in files:
            return
        files["workflow", item.name] = _normalized(item.file)
        for step in walk(item.steps):
            if step.kind == "call":
                workflow_file(catalog.workflow(step.target))
            for name in (
                (step.target,)
                if step.kind in {"agent", "gate"}
                else _BUILTIN_AGENTS.get(step.target, ())
            ):
                agent_file(name)

    workflow_file(workflow)
    digest = hashlib.sha256()
    for key, data in sorted(files.items()):
        digest.update(repr(key).encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def select_workflow(config: dict, name: str | None = None, saved: dict | None = None):
    """Resolve and validate the chosen definition, including resume identity."""
    catalog = config["customizations"]
    selected = (
        name
        or (saved or {}).get("name")
        or config.get("workflow", {}).get("default", "default")
    )
    try:
        workflow = catalog.workflow(selected)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    problems = validate(workflow, catalog)
    if problems:
        raise ValueError("\n".join(problems))
    if saved and (
        selected != saved["name"]
        or fingerprint(workflow, catalog) != saved["fingerprint"]
    ):
        raise ValueError(
            f"Workflow changed: {selected}. "
            "Restore its saved definitions before continuing."
        )
    return workflow

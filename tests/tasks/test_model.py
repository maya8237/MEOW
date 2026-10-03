"""Task graph validation for bounded parallel implementation."""

import pytest

from meow.tasks.model import TaskGraph, TaskSpec, load_task_graph


def test_one_task_graph_is_valid():
    graph = TaskGraph((TaskSpec("api", (), ("src/api",), ("pytest",)),))
    graph.validate()
    assert graph.ready(set()) == ("api",)


def test_dependency_controls_readiness():
    graph = TaskGraph((
        TaskSpec("api", (), ("src/api",), ("pytest",)),
        TaskSpec("ui", ("api",), ("src/ui",), ("npm test",)),
    ))
    graph.validate()
    assert graph.ready(set()) == ("api",)
    assert graph.ready({"api"}) == ("ui",)


@pytest.mark.parametrize(
    "tasks",
    [
        (TaskSpec("a", ("b",), ("a",), ()),),
        (TaskSpec("a", ("b",), ("a",), ()), TaskSpec("b", ("a",), ("b",), ())),
        (TaskSpec("a", (), ("a",), ()), TaskSpec("a", (), ("b",), ())),
        (TaskSpec("a", (), ("src",), ()), TaskSpec("b", (), ("src/api",), ())),
    ],
)
def test_invalid_graphs_are_rejected(tasks):
    with pytest.raises(ValueError):
        TaskGraph(tasks).validate()


def test_overlapping_paths_are_allowed_after_dependency():
    graph = TaskGraph((
        TaskSpec("a", (), ("src",), ()),
        TaskSpec("b", ("a",), ("src/api",), ()),
    ))
    graph.validate()


def test_optional_task_graph_file_is_validated(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    assert load_task_graph(plan) is None
    artifact = tmp_path / "plan.md.tasks.json"
    artifact.write_text(
        '{"tasks":[{"id":"api","depends_on":[],"owned_paths":["src/api"],'
        '"verification":["pytest tests/api"]}]}',
        encoding="utf-8",
    )
    graph = load_task_graph(plan)
    assert graph is not None
    assert graph.ready(set()) == ("api",)
    artifact.write_text(
        '{"tasks":[{"id":"bad","depends_on":["missing"],'
        '"owned_paths":["src"],"verification":[]}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_task_graph(plan)

"""Orchestrator task validation, parsing, and naming."""

import pytest

from meow.execution.orchestration.model import (
    MAX_ORCHESTRATOR_TASKS,
    OrchestratorRecord,
    OrchestratorTask,
    integration_branch,
    integration_worktree_name,
    is_orchestrator_id,
    new_orchestrator_id,
    parse_tasks_document,
    task_branch,
    task_worktree_name,
    validate_tasks,
)
from meow.tasks.model import (
    TaskGraph,
    TaskSpec,
    check_task_ids,
    dependency_ancestors,
    project_path,
)

ORCH_ID = "0123456789ab"


def test_validate_accepts_a_small_graph():
    validate_tasks([
        OrchestratorTask("api", "build api"),
        OrchestratorTask("ui", "build ui", depends_on=["api"]),
    ])


def test_validate_rejects_33_tasks():
    tasks = [OrchestratorTask(f"t{i}", "r") for i in range(MAX_ORCHESTRATOR_TASKS + 1)]
    with pytest.raises(ValueError, match="1 to 32"):
        validate_tasks(tasks)


def test_validate_accepts_exactly_32_tasks():
    validate_tasks([
        OrchestratorTask(f"t{i}", "r") for i in range(MAX_ORCHESTRATOR_TASKS)
    ])


def test_validate_rejects_zero_tasks():
    with pytest.raises(ValueError, match="1 to 32"):
        validate_tasks([])


def test_validate_rejects_case_insensitive_duplicates():
    with pytest.raises(ValueError, match="duplicate task ID"):
        validate_tasks([OrchestratorTask("API", "r"), OrchestratorTask("api", "r")])


def test_validate_rejects_cycle():
    tasks = [
        OrchestratorTask("a", "r", depends_on=["b"]),
        OrchestratorTask("b", "r", depends_on=["a"]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        validate_tasks(tasks)


def test_validate_rejects_self_dependency():
    with pytest.raises(ValueError, match="cycle"):
        validate_tasks([OrchestratorTask("a", "r", depends_on=["a"])])


def test_validate_rejects_unknown_dependency():
    with pytest.raises(ValueError, match="unknown dependency"):
        validate_tasks([OrchestratorTask("a", "r", depends_on=["ghost"])])


def test_validate_rejects_duplicate_dependency():
    tasks = [
        OrchestratorTask("a", "r"),
        OrchestratorTask("b", "r", depends_on=["a", "a"]),
    ]
    with pytest.raises(ValueError, match="duplicate dependencies"):
        validate_tasks(tasks)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "C:/x", ""])
def test_validate_rejects_non_project_relative_expected_path(path):
    with pytest.raises(ValueError, match="project-relative"):
        validate_tasks([OrchestratorTask("a", "r", expected_paths=[path])])


def test_overlapping_expected_paths_are_allowed():
    validate_tasks([
        OrchestratorTask("a", "r", expected_paths=["src/api.py"]),
        OrchestratorTask("b", "r", expected_paths=["src/api.py", "src"]),
    ])


def test_reserved_task_id_rejected_as_value_error():
    with pytest.raises(ValueError, match="reserved"):
        validate_tasks([OrchestratorTask("con", "r")])


def test_invalid_task_id_rejected():
    with pytest.raises(ValueError, match="invalid"):
        validate_tasks([OrchestratorTask("has space", "r")])


def test_blank_request_rejected():
    with pytest.raises(ValueError, match="needs a request"):
        validate_tasks([OrchestratorTask("a", "   ")])


def test_parse_valid_document_fills_defaults():
    raw = {
        "tasks": [
            {"id": "api", "request": "Build the api"},
            {
                "id": "ui",
                "request": "Build the ui",
                "depends_on": ["api"],
                "expected_paths": ["src/ui"],
                "source_ref": None,
            },
        ]
    }
    tasks = parse_tasks_document(raw)
    assert [task.id for task in tasks] == ["api", "ui"]
    assert tasks[0].depends_on == [] and tasks[0].expected_paths == []
    assert tasks[1].depends_on == ["api"]
    assert tasks[1].expected_paths == ["src/ui"]
    assert tasks[1].status == "pending" and tasks[1].cycles == 0


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {},
        {"tasks": "nope"},
        {"tasks": [], "extra": 1},
        {"tasks": ["not an object"]},
        {"tasks": [{"request": "no id"}]},
        {"tasks": [{"id": "a"}]},
        {"tasks": [{"id": "a", "request": ""}]},
        {"tasks": [{"id": "a", "request": "r", "depends_on": "b"}]},
        {"tasks": [{"id": "a", "request": "r", "expected_paths": [1]}]},
        {"tasks": [{"id": "a", "request": "r", "source_ref": 7}]},
    ],
)
def test_parse_rejects_malformed_documents(raw):
    with pytest.raises(ValueError):
        parse_tasks_document(raw)


def test_parse_rejects_unknown_entry_keys():
    raw = {"tasks": [{"id": "a", "request": "r", "owned_paths": ["x"]}]}
    with pytest.raises(ValueError, match="unknown keys: owned_paths"):
        parse_tasks_document(raw)


def test_parse_tasks_document_keeps_every_jira_key():
    raw = {"tasks": [{"id": "PROJ-1", "request": "a", "source_ref": "PROJ-1"}]}
    with pytest.raises(ValueError, match="PROJ-2"):
        parse_tasks_document(raw, jira_keys=["PROJ-1", "PROJ-2"])


def test_parse_rejects_unrequested_jira_keys():
    raw = {
        "tasks": [
            {"id": "PROJ-1", "request": "a", "source_ref": "PROJ-1"},
            {"id": "PROJ-9", "request": "b", "source_ref": "PROJ-9"},
        ]
    }
    with pytest.raises(ValueError, match="unrequested Jira keys: PROJ-9"):
        parse_tasks_document(raw, jira_keys=["PROJ-1"])


def test_parse_rejects_jira_task_without_source_ref():
    raw = {"tasks": [{"id": "PROJ-1", "request": "a"}]}
    with pytest.raises(ValueError, match="no source_ref"):
        parse_tasks_document(raw, jira_keys=["PROJ-1"])


def test_parse_requires_jira_task_id_to_be_the_sanitized_key():
    raw = {"tasks": [{"id": "login", "request": "a", "source_ref": "PROJ-1"}]}
    with pytest.raises(ValueError, match="must have id 'PROJ-1'"):
        parse_tasks_document(raw, jira_keys=["PROJ-1"])


def test_parse_accepts_a_complete_jira_set():
    raw = {
        "tasks": [
            {"id": "PROJ-1", "request": "a", "source_ref": "PROJ-1"},
            {
                "id": "PROJ-2",
                "request": "b",
                "source_ref": "PROJ-2",
                "depends_on": ["PROJ-1"],
            },
        ]
    }
    tasks = parse_tasks_document(raw, jira_keys=["PROJ-1", "PROJ-2"])
    assert [task.source_ref for task in tasks] == ["PROJ-1", "PROJ-2"]


def test_branch_names():
    assert integration_branch("orchestrator/", ORCH_ID) == f"orchestrator/{ORCH_ID}"
    assert task_branch("orchestrator/", ORCH_ID, "api") == f"orchestrator/{ORCH_ID}-api"
    assert task_branch("team-", ORCH_ID, "api") == f"team-{ORCH_ID}-api"
    assert integration_worktree_name(ORCH_ID) == f"orch-{ORCH_ID}"
    assert task_worktree_name(ORCH_ID, "api") == f"orch-{ORCH_ID}-api"


def test_ids_are_distinct_from_run_ids():
    assert is_orchestrator_id(new_orchestrator_id())
    assert not is_orchestrator_id("0" * 32)
    assert not is_orchestrator_id("0123456789AB")
    assert not is_orchestrator_id("../x")


def test_record_task_lookup():
    record = OrchestratorRecord("a" * 12, "obj", "objective", "main", "cli")
    record.tasks.append(OrchestratorTask("api", "r"))
    assert record.task("api").request == "r"
    with pytest.raises(KeyError):
        record.task("ui")
    assert record.phase == "decomposing" and record.schema_version == 1


def test_record_defaults_are_independent_per_instance():
    first = OrchestratorRecord("a" * 12, "obj", "objective", "main", "cli")
    second = OrchestratorRecord("b" * 12, "obj", "objective", "main", "cli")
    first.integration["merged"].append("api")
    first.options["pr"] = False
    assert second.integration["merged"] == []
    assert second.options == {"test": False, "pr": True}


def test_shared_validators_keep_task_graph_messages():
    with pytest.raises(ValueError, match="duplicate task ID"):
        check_task_ids(["a", "A"])
    with pytest.raises(ValueError, match="task dependency cycle"):
        dependency_ancestors({"a": ["b"], "b": ["a"]})
    assert dependency_ancestors({"a": [], "b": ["a"], "c": ["b"]}) == {
        "a": set(),
        "b": {"a"},
        "c": {"a", "b"},
    }
    assert project_path("Src\\Api") == "src/api"
    graph = TaskGraph((
        TaskSpec("a", (), ("src",), ()),
        TaskSpec("b", (), ("src/x",), ()),
    ))
    with pytest.raises(ValueError, match="overlap"):
        graph.validate()

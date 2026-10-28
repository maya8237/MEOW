import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from meow.agents.orchestration import OrchestratorAgents
from meow.execution.orchestration.intake import (
    IntakeRequest,
    decompose,
    dedupe_keys,
    fetch_jira_tasks,
    jira_task,
    load_tasks_file,
    save_decomposed,
)
from meow.execution.orchestration.model import parse_tasks_document
from meow.execution.orchestration.store import OrchestratorStore

JIRA_CONFIG = {
    "jira": {"project_key": "PROJ", "mcp": {"command": "uvx", "args": []}},
}
ISSUES = [
    {"key": "PROJ-1", "summary": "Fix login", "description": "Steps to reproduce"},
    {"key": "PROJ-2", "summary": "Fix logout", "description": "Expected: signed out"},
]
TWO_TASKS = {
    "tasks": [
        {"id": "api", "request": "Build the API"},
        {"id": "ui", "request": "Build the UI", "depends_on": ["api"]},
    ]
}


class StubDecomposer:
    """Writes the next prepared document on each call and records the call."""

    def __init__(self, *documents):
        self.documents = list(documents)
        self.calls: list[dict] = []

    async def run(self, objective, output_file, *, task_drafts=None, correction=None):
        self.calls.append({
            "objective": objective,
            "task_drafts": task_drafts,
            "correction": correction,
        })
        document = self.documents[len(self.calls) - 1]
        text = document if isinstance(document, str) else json.dumps(document)
        output_file.write_text(text, encoding="utf-8")
        return output_file


class StubFetcher:
    server = "jira"
    role = "issue_fetcher"

    def __init__(self, issues):
        self.issues = issues
        self.checks = 0
        self.fetched: list[str] = []
        self.jql = None

    async def check_active(self):
        self.checks += 1

    async def fetch(self, issue_key, project_key, output_file):
        self.fetched.append(issue_key)
        issue = next(i for i in self.issues if i["key"].lower() == issue_key.lower())
        output_file.write_text(json.dumps(issue), encoding="utf-8")

    async def fetch_many(self, jql, output_file):
        self.jql = jql
        output_file.write_text(json.dumps({"issues": self.issues}), encoding="utf-8")


def with_decomposer(stub) -> OrchestratorAgents:
    return OrchestratorAgents(decomposer=lambda context: stub)


def with_fetcher(stub) -> OrchestratorAgents:
    return OrchestratorAgents(issue_fetcher=lambda context, mcp: stub)


@dataclass
class Decomposing:
    """A journaled orchestrator in the `decomposing` phase."""

    store: OrchestratorStore
    orch_id: str
    path: Path

    def run(self, stub, drafts=None):
        return asyncio.run(
            decompose(
                self.path,
                {},
                self.store,
                self.orch_id,
                workdir=self.path,
                drafts=drafts,
                agents=with_decomposer(stub),
            )
        )

    def record(self):
        return self.store.load(self.orch_id)


def decomposing(tmp_path) -> Decomposing:
    store = OrchestratorStore(tmp_path)
    record = store.create(
        objective="Build it",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )
    return Decomposing(store, record.id, tmp_path)


def jira_drafts():
    return [jira_task(issue) for issue in ISSUES]


def test_exactly_one_source_is_required():
    IntakeRequest("objective", objective="Build it").validate()
    IntakeRequest("jira", jira_keys=("PROJ-1",)).validate()
    IntakeRequest("jql", jql="project = PROJ").validate()
    IntakeRequest("tasks", tasks_file=Path("tasks.toml")).validate()
    for request in (
        IntakeRequest("objective"),
        IntakeRequest("objective", objective="  "),
        IntakeRequest("objective", objective="Build it", jql="project = PROJ"),
        IntakeRequest("jira", objective="Build it"),
        IntakeRequest("tasks", jira_keys=("PROJ-1",), tasks_file=Path("t.toml")),
    ):
        with pytest.raises(ValueError, match="Give exactly one input"):
            request.validate()


def test_tasks_file_toml_and_json_load_identically(tmp_path):
    toml_file = tmp_path / "tasks.toml"
    toml_file.write_text(
        '[[tasks]]\nid = "api"\nrequest = "Build the API"\n\n'
        '[[tasks]]\nid = "ui"\nrequest = "Build the UI"\ndepends_on = ["api"]\n',
        encoding="utf-8",
    )
    json_file = tmp_path / "tasks.json"
    json_file.write_text(json.dumps(TWO_TASKS), encoding="utf-8")

    from_toml = load_tasks_file(toml_file)

    assert from_toml == load_tasks_file(json_file)
    assert [(t.id, t.depends_on) for t in from_toml] == [("api", []), ("ui", ["api"])]


@pytest.mark.parametrize("suffix", [".toml", ".json"])
def test_tasks_file_over_cap_fails_with_narrowing_message(tmp_path, suffix):
    path = tmp_path / f"tasks{suffix}"
    if suffix == ".toml":
        path.write_text(
            "".join(f'[[tasks]]\nid = "t{i}"\nrequest = "r"\n' for i in range(33)),
            encoding="utf-8",
        )
    else:
        tasks = [{"id": f"t{i}", "request": "r"} for i in range(33)]
        path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")

    with pytest.raises(
        ValueError, match="33 tasks is more than the 32 allowed; narrow"
    ):
        load_tasks_file(path)


def test_tasks_file_problems_are_reported(tmp_path):
    with pytest.raises(ValueError, match=r"Unsupported tasks file t\.yaml"):
        load_tasks_file(tmp_path / "t.yaml")
    with pytest.raises(ValueError, match="Cannot read tasks file"):
        load_tasks_file(tmp_path / "missing.toml")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken\.json is not valid JSON"):
        load_tasks_file(broken)
    cyclic = tmp_path / "cyclic.json"
    cyclic.write_text(
        json.dumps({
            "tasks": [
                {"id": "a", "request": "r", "depends_on": ["b"]},
                {"id": "b", "request": "r", "depends_on": ["a"]},
            ]
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cycle"):
        load_tasks_file(cyclic)


def test_jira_task_request_text():
    task = jira_task({"key": "PROJ-7", "summary": "Fix login", "description": "Steps"})

    assert task.request == "Resolve Jira issue PROJ-7: Fix login\n\nSteps"
    assert (task.id, task.source_ref) == ("PROJ-7", "PROJ-7")


def test_duplicate_jira_keys_deduplicated():
    assert dedupe_keys(["PROJ-1", "proj-1", " PROJ-2 ", ""]) == ("PROJ-1", "PROJ-2")


def test_jira_keys_are_fetched_once_each_after_one_preflight(tmp_path):
    stub = StubFetcher(ISSUES)
    request = IntakeRequest("jira", jira_keys=("PROJ-1", "proj-1", "PROJ-2"))

    tasks = asyncio.run(
        fetch_jira_tasks(tmp_path, JIRA_CONFIG, request, with_fetcher(stub))
    )

    assert [t.id for t in tasks] == ["PROJ-1", "PROJ-2"]
    assert tasks[1].request == (
        "Resolve Jira issue PROJ-2: Fix logout\n\nExpected: signed out"
    )
    assert (stub.checks, stub.fetched) == (1, ["PROJ-1", "PROJ-2"])


def test_jql_fetch_uses_issue_list(tmp_path):
    stub = StubFetcher(ISSUES)
    request = IntakeRequest("jql", jql="project = PROJ AND status = Open")

    tasks = asyncio.run(
        fetch_jira_tasks(tmp_path, JIRA_CONFIG, request, with_fetcher(stub))
    )

    assert [t.source_ref for t in tasks] == ["PROJ-1", "PROJ-2"]
    assert stub.jql == "project = PROJ AND status = Open"
    assert stub.fetched == []


def test_jql_without_matches_fails(tmp_path):
    request = IntakeRequest("jql", jql="project = NONE")

    with pytest.raises(RuntimeError, match="No Jira issues matched"):
        asyncio.run(
            fetch_jira_tasks(
                tmp_path, JIRA_CONFIG, request, with_fetcher(StubFetcher([]))
            )
        )


def test_jql_over_cap_fails_with_narrowing_message(tmp_path):
    many = [{"key": f"PROJ-{i}", "summary": "s", "description": "d"} for i in range(33)]
    request = IntakeRequest("jql", jql="project = PROJ")

    with pytest.raises(ValueError, match="narrow"):
        asyncio.run(
            fetch_jira_tasks(
                tmp_path, JIRA_CONFIG, request, with_fetcher(StubFetcher(many))
            )
        )


def test_jira_intake_requires_jira_configuration(tmp_path):
    request = IntakeRequest("jira", jira_keys=("PROJ-1",))

    with pytest.raises(ValueError, match=r"No jira MCP server"):
        asyncio.run(
            fetch_jira_tasks(tmp_path, {}, request, with_fetcher(StubFetcher([])))
        )


def test_decomposed_objective_is_validated_and_journaled(tmp_path):
    case = decomposing(tmp_path)
    stub = StubDecomposer(TWO_TASKS)

    tasks = case.run(stub)

    record = case.record()
    written = case.store.dir_for(case.orch_id) / "tasks.json"
    assert [t.id for t in tasks] == ["api", "ui"]
    assert [t.id for t in record.tasks] == ["api", "ui"]
    assert record.intake["attempts"] == len(stub.calls)
    assert json.loads(written.read_text(encoding="utf-8")) == TWO_TASKS
    assert stub.calls == [
        {"objective": "Build it", "task_drafts": None, "correction": None}
    ]


def test_decomposer_corrective_retry_then_success(tmp_path):
    case = decomposing(tmp_path)
    stub = StubDecomposer({}, TWO_TASKS)
    with pytest.raises(ValueError) as rejected:
        parse_tasks_document({})

    tasks = case.run(stub)

    assert [t.id for t in tasks] == ["api", "ui"]
    assert [call["correction"] for call in stub.calls] == [None, str(rejected.value)]
    assert case.record().intake["attempts"] == len(stub.calls)


def test_decomposer_output_that_is_not_json_gets_a_retry(tmp_path):
    case = decomposing(tmp_path)
    stub = StubDecomposer("{not json", TWO_TASKS)

    case.run(stub)

    assert "tasks.json is not valid JSON" in stub.calls[1]["correction"]


def test_decomposer_invalid_twice_raises(tmp_path):
    case = decomposing(tmp_path)
    stub = StubDecomposer({}, {})

    with pytest.raises(ValueError, match="invalid after 2 attempts"):
        case.run(stub)

    record = case.record()
    assert (record.intake["attempts"], record.tasks) == (len(stub.calls), [])
    assert [call["correction"] is None for call in stub.calls] == [True, False]


def test_jira_set_decomposer_cannot_drop_keys(tmp_path):
    case = decomposing(tmp_path)
    dropped = {"tasks": [{"id": "PROJ-1", "request": "r", "source_ref": "PROJ-1"}]}
    stub = StubDecomposer(dropped, dropped)

    with pytest.raises(ValueError, match="PROJ-2"):
        case.run(stub, drafts=jira_drafts())

    assert "PROJ-2" in stub.calls[1]["correction"]
    assert [t.id for t in case.record().tasks] == ["PROJ-1", "PROJ-2"]


def test_jira_set_decomposer_cannot_add_tasks(tmp_path):
    case = decomposing(tmp_path)
    extra = {
        "tasks": [
            {"id": "PROJ-1", "request": "r", "source_ref": "PROJ-1"},
            {"id": "PROJ-2", "request": "r", "source_ref": "PROJ-2"},
            {"id": "extra", "request": "r"},
        ]
    }
    stub = StubDecomposer(extra, extra)

    with pytest.raises(ValueError, match="invalid after 2 attempts"):
        case.run(stub, drafts=jira_drafts())

    assert [t.id for t in case.record().tasks] == ["PROJ-1", "PROJ-2"]


def test_jira_set_keeps_fetched_request_text(tmp_path):
    case = decomposing(tmp_path)
    rewritten = {
        "tasks": [
            {"id": "PROJ-1", "request": "rewritten", "source_ref": "PROJ-1"},
            {
                "id": "PROJ-2",
                "request": "rewritten too",
                "source_ref": "PROJ-2",
                "depends_on": ["PROJ-1"],
                "expected_paths": ["src/auth.py"],
            },
        ]
    }
    stub = StubDecomposer(rewritten)
    drafts = jira_drafts()

    tasks = case.run(stub, drafts=drafts)

    assert [t.request for t in tasks] == [d.request for d in drafts]
    assert (tasks[1].depends_on, tasks[1].expected_paths) == (
        ["PROJ-1"],
        ["src/auth.py"],
    )
    assert stub.calls[0]["task_drafts"].startswith(
        "Task PROJ-1 (PROJ-1):\nResolve Jira"
    )
    record = case.record()
    assert record.intake["jira_keys"] == ["PROJ-1", "PROJ-2"]
    assert [t.request for t in record.tasks] == [d.request for d in drafts]


def test_save_decomposed_rejects_over_cap_documents(tmp_path):
    case = decomposing(tmp_path)
    document = {"tasks": [{"id": f"t{i}", "request": "r"} for i in range(33)]}

    with pytest.raises(ValueError, match="narrow"):
        save_decomposed(case.store, case.orch_id, document)

    assert case.record().tasks == []


def test_save_decomposed_without_recorded_jira_drafts_is_rejected(tmp_path):
    case = decomposing(tmp_path)
    case.store.update(
        case.orch_id, lambda record: record.intake.update(jira_keys=["PROJ-1"])
    )
    document = {"tasks": [{"id": "PROJ-1", "request": "r", "source_ref": "PROJ-1"}]}

    with pytest.raises(ValueError, match="not recorded before decomposing"):
        save_decomposed(case.store, case.orch_id, document)

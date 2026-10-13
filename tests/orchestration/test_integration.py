"""Merging passed tasks into the orchestrator integration branch."""

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from meow.execution.orchestration import integration
from meow.execution.orchestration.integration import (
    MergeOutcome,
    abort_merge,
    begin_merge,
    ensure_integration_worktree,
    finish_merge,
    has_conflict_markers,
    push_integration,
    reset_mid_merge,
)
from meow.execution.orchestration.model import (
    OrchestratorRecord,
    OrchestratorTask,
    task_branch,
)
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import CheckResult
from meow.infrastructure.checks import Check

PREFIX = "orchestrator/"
MERGE_PARENTS = 2
NO_CHECKS = {"lint": [], "tester": {"tests": []}, "build": []}
BUILD_CHECK = {
    "lint": [],
    "tester": {"tests": []},
    "build": [Check("build", "build", "make")],
}


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@dataclass
class Scenario:
    """A repository with an orchestrator and its integration worktree."""

    repo: Path
    remote: Path
    store: OrchestratorStore
    oid: str
    worktree: Path

    @property
    def record(self) -> OrchestratorRecord:
        return self.store.load(self.oid)

    def head(self) -> str:
        return git(self.worktree, "rev-parse", "HEAD")

    def parents(self) -> int:
        return len(git(self.worktree, "log", "-1", "--format=%P").split())

    def git_dir(self) -> Path:
        return Path(git(self.worktree, "rev-parse", "--absolute-git-dir"))

    def add_task(
        self, task_id: str, files: dict[str, str], source_ref: str | None = None
    ) -> None:
        """A passed task whose branch starts at the integration tip."""
        record = self.record
        branch = task_branch(PREFIX, self.oid, task_id)
        path = self.repo.parent / f"task-{task_id}"
        base = record.integration["branch"]
        git(self.repo, "worktree", "add", "-b", branch, str(path), base)
        for name, text in files.items():
            (path / name).write_text(text, encoding="utf-8")
        if files:
            git(path, "add", "-A")
            git(path, "commit", "-qm", f"task {task_id}")
        record.tasks.append(
            OrchestratorTask(
                id=task_id,
                request=f"do {task_id}",
                source_ref=source_ref,
                status="passed",
                branch=branch,
                worktree=str(path),
            )
        )
        self.store.save(record)

    def begin(self, task_id: str) -> MergeOutcome:
        return begin_merge(self.repo, self.store, self.oid, task_id)

    def finish(self, task_id: str, config: dict | None = None) -> MergeOutcome:
        return asyncio.run(
            finish_merge(self.repo, self.store, self.oid, task_id, config or NO_CHECKS)
        )


def start(git_repo, base_files: dict[str, str] | None = None) -> Scenario:
    repo, remote = git_repo
    for name, text in (base_files or {}).items():
        (repo / name).write_text(text, encoding="utf-8")
        git(repo, "add", name)
        git(repo, "commit", "-qm", f"base {name}")
    store = OrchestratorStore(repo)
    record = store.create(
        objective="objective",
        source="objective",
        base_branch=git(repo, "branch", "--show-current"),
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )
    worktree = ensure_integration_worktree(repo, record, PREFIX)
    store.save(record)
    return Scenario(repo, remote, store, record.id, worktree)


def conflicted(git_repo) -> Scenario:
    """Tasks `api` (merged) and `ui` (open merge, conflicting in shared.py)."""
    scenario = start(git_repo, {"shared.py": "base\n"})
    scenario.add_task("api", {"shared.py": "api\n"})
    scenario.add_task("ui", {"shared.py": "ui\n"})
    assert scenario.begin("api").status == "merged"
    assert scenario.finish("api").status == "merged"
    assert scenario.begin("ui").status == "conflict"
    return scenario


def test_ensure_integration_worktree_branches_from_base(git_repo):
    scenario = start(git_repo)
    record = scenario.record

    assert scenario.worktree == scenario.repo / ".worktrees" / f"orch-{record.id}"
    assert git(scenario.worktree, "branch", "--show-current") == (
        f"orchestrator/{record.id}"
    )
    assert scenario.head() == git(scenario.repo, "rev-parse", "HEAD")
    assert record.integration["branch"] == f"orchestrator/{record.id}"
    assert record.integration["worktree"] == str(scenario.worktree)
    again = ensure_integration_worktree(scenario.repo, record, PREFIX)
    assert again == scenario.worktree


def test_clean_merge_records_pre_merge_and_merges(git_repo):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"}, source_ref="PROJ-1")
    tip = scenario.head()

    outcome = scenario.begin("api")

    assert outcome.status == "merged"
    assert outcome.commit == scenario.head() != tip
    record = scenario.record
    assert record.integration["pre_merge_commit"] == tip
    assert record.integration["merging"] == "api"
    assert git(scenario.worktree, "log", "-1", "--format=%s") == (
        "Merge orchestrator task api (PROJ-1)"
    )
    assert scenario.parents() == MERGE_PARENTS
    assert outcome.log.name == "integration-api-1.log"
    assert scenario.finish("api").status == "merged"
    record = scenario.record
    assert record.task("api").status == "merged"
    assert record.integration["merged"] == ["api"]
    assert record.integration["merging"] is None


def test_task_without_changes_merges_as_noop(git_repo):
    scenario = start(git_repo)
    scenario.add_task("api", {})
    tip = scenario.head()

    assert scenario.begin("api").status == "noop"
    finished = scenario.finish("api")

    assert finished.status == "noop"
    assert finished.commit == tip == scenario.head()
    record = scenario.record
    assert record.task("api").status == "merged"
    assert record.integration["merged"] == ["api"]


def test_begin_merge_requires_a_passed_task_and_no_open_merge(git_repo):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})
    scenario.add_task("ui", {"ui.py": "y\n"})
    scenario.store.update(
        scenario.oid, lambda r: setattr(r.task("ui"), "status", "running")
    )

    with pytest.raises(ValueError, match="Task ui is running, not passed"):
        scenario.begin("ui")
    scenario.begin("api")
    scenario.store.update(
        scenario.oid, lambda r: setattr(r.task("ui"), "status", "passed")
    )
    with pytest.raises(RuntimeError, match="Merge of task api is still open"):
        scenario.begin("ui")


def test_conflict_reports_paths_and_keeps_merge_open(git_repo):
    scenario = start(git_repo, {"shared.py": "base\n"})
    scenario.add_task("api", {"shared.py": "api\n"})
    scenario.add_task("ui", {"shared.py": "ui\n"})
    scenario.begin("api")
    scenario.finish("api")

    outcome = scenario.begin("ui")

    assert outcome.status == "conflict"
    assert outcome.conflicted_paths == ("shared.py",)
    assert (scenario.git_dir() / "MERGE_HEAD").exists()
    assert has_conflict_markers(scenario.worktree / "shared.py")
    assert scenario.record.integration["merging"] == "ui"


def test_finish_without_an_open_merge_is_rejected(git_repo):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})

    with pytest.raises(RuntimeError, match="No open merge for task api"):
        scenario.finish("api")


def test_leftover_markers_refuse_finish(git_repo):
    scenario = conflicted(git_repo)

    outcome = scenario.finish("ui")

    assert outcome.status == "conflict"
    assert outcome.conflicted_paths == ("shared.py",)
    record = scenario.record
    assert record.integration["merging"] == "ui"
    assert record.task("ui").status == "passed"


def test_resolved_conflict_finishes_merge(git_repo):
    scenario = conflicted(git_repo)
    (scenario.worktree / "shared.py").write_text("api\nui\n", encoding="utf-8")

    outcome = scenario.finish("ui")

    assert outcome.status == "merged"
    assert git(scenario.worktree, "status", "--porcelain") == ""
    assert git(scenario.worktree, "show", "HEAD:shared.py") == "api\nui"
    assert git(scenario.worktree, "log", "-1", "--format=%s") == (
        "Merge orchestrator task ui"
    )
    assert scenario.parents() == MERGE_PARENTS
    assert scenario.record.integration["merged"] == ["api", "ui"]


def test_failing_post_merge_check_rolls_back(git_repo, monkeypatch):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})
    tip = scenario.head()
    failing = CheckResult("build", "make", True, 1, 0.1, "boom output", "r", "f")

    async def fake_checks(directory, config):  # ruff: ignore[unused-async]
        return [failing]

    monkeypatch.setattr(integration, "run_final_checks", fake_checks)
    scenario.begin("api")

    outcome = scenario.finish("api", BUILD_CHECK)

    assert outcome.status == "checks_failed"
    assert scenario.head() == tip
    assert not (scenario.worktree / "api.py").exists()
    assert "boom output" in outcome.log.read_text(encoding="utf-8")
    record = scenario.record
    assert record.integration["merging"] is None
    assert record.task("api").status == "passed"
    assert record.integration["merged"] == []


def test_failing_optional_check_does_not_roll_back(git_repo, monkeypatch):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})
    advisory = CheckResult("lint", "fallow", False, 1, 0.1, "warn", "r", "f")

    async def fake_checks(directory, config):  # ruff: ignore[unused-async]
        return [advisory]

    monkeypatch.setattr(integration, "run_final_checks", fake_checks)
    scenario.begin("api")

    assert scenario.finish("api", BUILD_CHECK).status == "merged"


def test_no_checks_configured_skips_check_run(git_repo, monkeypatch):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})

    async def forbidden(directory, config):  # ruff: ignore[unused-async]
        raise AssertionError("checks must not run")

    monkeypatch.setattr(integration, "run_final_checks", forbidden)
    scenario.begin("api")

    assert scenario.finish("api").status == "merged"


def test_reset_mid_merge_restores_pre_merge_commit(git_repo):
    scenario = conflicted(git_repo)
    pre_merge = scenario.record.integration["pre_merge_commit"]

    assert reset_mid_merge(scenario.repo, scenario.store, scenario.oid) == "ui"

    assert not (scenario.git_dir() / "MERGE_HEAD").exists()
    assert scenario.head() == pre_merge
    assert not has_conflict_markers(scenario.worktree / "shared.py")
    assert git(scenario.worktree, "status", "--porcelain") == ""
    record = scenario.record
    assert record.integration["merging"] is None
    assert record.task("ui").status == "passed"


def test_reset_mid_merge_discards_a_staged_resolution(git_repo):
    scenario = conflicted(git_repo)
    pre_merge = scenario.record.integration["pre_merge_commit"]
    (scenario.worktree / "shared.py").write_text("api\nui\n", encoding="utf-8")
    git(scenario.worktree, "add", "shared.py")

    assert reset_mid_merge(scenario.repo, scenario.store, scenario.oid) == "ui"

    assert scenario.head() == pre_merge
    assert git(scenario.worktree, "show", "HEAD:shared.py") == "api"
    assert (scenario.worktree / "shared.py").read_text(encoding="utf-8") == "api\n"
    assert git(scenario.worktree, "status", "--porcelain") == ""


def test_reset_mid_merge_undoes_a_merge_that_was_never_finished(git_repo):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})
    tip = scenario.head()
    scenario.begin("api")

    assert reset_mid_merge(scenario.repo, scenario.store, scenario.oid) == "api"

    assert scenario.head() == tip
    assert not (scenario.worktree / "api.py").exists()
    assert scenario.record.task("api").status == "passed"


def test_reset_without_open_merge_does_nothing(git_repo):
    scenario = start(git_repo)

    assert reset_mid_merge(scenario.repo, scenario.store, scenario.oid) is None


def test_abort_merge_is_idempotent_and_checks_the_task(git_repo):
    scenario = conflicted(git_repo)
    arguments = (scenario.repo, scenario.store, scenario.oid)

    with pytest.raises(RuntimeError, match="open merge is for task ui, not api"):
        abort_merge(*arguments, "api")
    abort_merge(*arguments, "ui")
    abort_merge(*arguments, "ui")

    assert scenario.record.integration["merging"] is None


def test_each_merge_attempt_gets_its_own_log(git_repo):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})

    first = scenario.begin("api")
    abort_merge(scenario.repo, scenario.store, scenario.oid, "api")
    second = scenario.begin("api")

    assert (first.log.name, second.log.name) == (
        "integration-api-1.log",
        "integration-api-2.log",
    )


def test_log_numbers_ignore_tasks_with_a_longer_id(tmp_path):
    (tmp_path / "integration-a-1-4.log").write_text("x", encoding="utf-8")

    assert integration._new_log(tmp_path, "a").name == "integration-a-1.log"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<<<<<<< HEAD\nours\n", True),
        ("one\n=======\ntwo\n", True),
        (">>>>>>> orchestrator/x\n", True),
        ("<<<<<<<\n", True),
        ("code <<<<<<< not at line start\n", False),
        ("========\n", False),
        ("plain\n", False),
    ],
)
def test_has_conflict_markers(tmp_path, text, expected):
    path = tmp_path / "file.txt"
    path.write_text(text, encoding="utf-8")

    assert has_conflict_markers(path) is expected


def test_has_conflict_markers_of_missing_file_is_false(tmp_path):
    assert has_conflict_markers(tmp_path / "gone.txt") is False


def test_merge_without_git_identity_falls_back_to_meow(git_repo, monkeypatch):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})
    git(scenario.repo, "config", "--unset", "user.name")
    git(scenario.repo, "config", "--unset", "user.email")
    git(scenario.repo, "config", "user.useConfigOnly", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    outcome = scenario.begin("api")

    assert outcome.status == "merged"
    assert git(scenario.worktree, "log", "-1", "--format=%an") == "MEOW"


def test_push_is_idempotent(git_repo, monkeypatch):
    scenario = start(git_repo)
    scenario.add_task("api", {"api.py": "x\n"})
    scenario.begin("api")
    scenario.finish("api")
    pushes = []
    real_git = integration._git

    def spy(directory, *args, **kwargs):
        if args[0] == "push":
            pushes.append(args)
        return real_git(directory, *args, **kwargs)

    monkeypatch.setattr(integration, "_git", spy)
    arguments = (scenario.repo, scenario.store, scenario.oid)

    first = push_integration(*arguments)
    second = push_integration(*arguments)

    branch = f"refs/heads/orchestrator/{scenario.oid}"
    assert first == second == scenario.head()
    assert git(scenario.remote, "rev-parse", branch) == first
    assert len(pushes) == 1
    assert scenario.record.delivery["pushed_commit"] == first


def test_push_without_origin_raises(git_repo):
    scenario = start(git_repo)
    git(scenario.repo, "remote", "remove", "origin")

    with pytest.raises(RuntimeError, match="No origin remote configured"):
        push_integration(scenario.repo, scenario.store, scenario.oid)

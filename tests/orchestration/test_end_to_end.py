"""One whole objective through the CLI driver with real git: a dependency, a
forced merge conflict, objective review, push, and a pull request."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from meow.execution.orchestration.report import render_report
from tests.orchestration.driver_fakes import PR_URL, AgentScript, World, task_dict
from tests.orchestration.fakes import git


@pytest.fixture
def delivered(tmp_path, monkeypatch):
    """Run the objective `api` -> `ui` to delivery.

    `ui` edits `shared.txt`, and while it runs a sibling commit lands on the
    integration branch changing the same file, so merging `ui` conflicts and
    the scripted integrator resolves it."""
    script = AgentScript(
        [task_dict("api"), task_dict("ui", depends_on=["api"])],
        resolutions={"shared.txt": "api\nsibling\nui\n"},
    )
    outcomes = {
        "api": [("pass", {"shared.txt": "api\n", "api.py": "API = 1\n"})],
        "ui": [("pass", {"shared.txt": "api\nui\n", "ui.py": "UI = 1\n"})],
    }
    world = World(tmp_path, monkeypatch, script, outcomes)

    def inject_sibling_commit():
        worktree = Path(world.store.latest().integration["worktree"])
        (worktree / "shared.txt").write_text("api\nsibling\n", encoding="utf-8")
        git(worktree, "add", "shared.txt")
        git(worktree, "commit", "-qm", "sibling work")

    world.sim.before["ui", 0] = inject_sibling_commit
    oid, code = world.start()
    return SimpleNamespace(
        world=world, script=script, oid=oid, code=code, record=world.record(oid)
    )


def test_delivers_both_tasks_with_the_conflict_resolved(delivered):
    remote, branch = delivered.world.remote, f"orchestrator/{delivered.oid}"

    assert delivered.code == 0
    assert delivered.record.phase == "delivered"
    assert [t.status for t in delivered.record.tasks] == ["merged", "merged"]
    assert delivered.script.integrator_calls == [["shared.txt"]]
    assert git(remote, "show", f"{branch}:shared.txt") == "api\nsibling\nui"
    assert git(remote, "show", f"{branch}:api.py") == "API = 1"
    assert git(remote, "show", f"{branch}:ui.py") == "UI = 1"


def test_pushes_only_the_integration_branch(delivered):
    remote, branch = delivered.world.remote, f"orchestrator/{delivered.oid}"
    integration = Path(delivered.record.integration["worktree"])

    assert git(remote, "for-each-ref", "--format=%(refname)") == f"refs/heads/{branch}"
    subjects = git(integration, "log", "--format=%s").splitlines()
    assert "Merge orchestrator task api" in subjects
    assert "Merge orchestrator task ui" in subjects


def test_task_branches_coexist_and_ui_saw_api_work(delivered):
    repo, branch = delivered.world.repo, f"orchestrator/{delivered.oid}"
    ui_worktree = delivered.world.sim.launcher.launches[1]["worktree"]

    assert git(repo, "rev-parse", "--verify", f"{branch}-api")
    assert git(repo, "rev-parse", "--verify", f"{branch}-ui")
    assert (ui_worktree / "api.py").exists()


def test_review_pull_request_and_report(delivered):
    branch = f"orchestrator/{delivered.oid}"
    title, body, head, base, _output = delivered.script.pull_request.calls[0]
    store = delivered.world.store
    report = (store.dir_for(delivered.oid) / "report.md").read_text(encoding="utf-8")

    assert delivered.script.review.reviews[0][:2] == ("main", branch)
    assert delivered.record.delivery["pr"] == {"url": PR_URL, "provider": "github"}
    assert (title, head, base) == ("Orchestrator: Build it", branch, "main")
    assert "Resolved conflicts in shared.txt" in body
    assert report.startswith(f"# Orchestrator {delivered.oid}: delivered")
    assert "## Conflicts resolved\n\n- ui: Resolved conflicts in shared.txt" in report
    assert render_report(delivered.world.repo, delivered.record).startswith(
        "# Orchestrator"
    )

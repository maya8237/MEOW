"""One whole objective through the CLI driver with real git: a dependency, a
forced merge conflict, objective review, push, and a pull request."""

from pathlib import Path

from meow.execution.orchestration.report import render_report
from tests.orchestration.driver_fakes import PR_URL, AgentScript, World, task_dict
from tests.orchestration.fakes import git


def test_delivers_tasks_with_conflict_resolution_push_and_review(  # ruff: ignore[too-many-statements,too-many-locals] -- assert one real Git workflow without rebuilding it
    tmp_path, monkeypatch
):
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
    record = world.record(oid)
    remote, branch = world.remote, f"orchestrator/{oid}"

    assert code == 0
    assert record.phase == "delivered"
    assert [t.status for t in record.tasks] == ["merged", "merged"]
    assert script.integrator_calls == [["shared.txt"]]
    assert git(remote, "show", f"{branch}:shared.txt") == "api\nsibling\nui"
    assert git(remote, "show", f"{branch}:api.py") == "API = 1"
    assert git(remote, "show", f"{branch}:ui.py") == "UI = 1"
    integration = Path(record.integration["worktree"])

    assert git(remote, "for-each-ref", "--format=%(refname)") == f"refs/heads/{branch}"
    subjects = git(integration, "log", "--format=%s").splitlines()
    assert "Merge orchestrator task api" in subjects
    assert "Merge orchestrator task ui" in subjects
    repo = world.repo
    ui_worktree = world.sim.launcher.launches[1]["worktree"]

    branches = git(repo, "branch", "--format=%(refname:short)").splitlines()
    assert f"{branch}-api" not in branches
    assert f"{branch}-ui" not in branches
    assert not ui_worktree.exists()
    assert (integration / "api.py").exists()
    title, body, head, base, _output = script.pull_request.calls[0]
    report = (world.store.dir_for(oid) / "report.md").read_text(encoding="utf-8")

    assert script.review.reviews[0][:2] == ("main", branch)
    assert record.delivery["pr"] == {"url": PR_URL, "provider": "github"}
    assert (title, head, base) == ("Orchestrator: Build it", branch, "main")
    assert "Resolved conflicts in shared.txt" in body
    assert report.startswith(f"# Orchestrator {oid}: delivered")
    assert "## Conflicts resolved\n\n- ui: Resolved conflicts in shared.txt" in report
    assert render_report(repo, record).startswith("# Orchestrator")

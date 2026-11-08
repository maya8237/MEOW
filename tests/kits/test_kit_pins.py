import asyncio
import json
import os
import shutil

import pytest

from meow.cli import resume_cli
from meow.execution.run_state import RunStore
from meow.project.config import load_config
from meow.project.custom import kit_ops
from meow.project.custom.kit_pins import active_run_shas, pinned
from meow.project.custom.kit_store import normalize_url
from meow.project.custom.kits import PINS_ENV, kits_home, read_lock

from .conftest import SKILL

AGENT = 'description = "{d}"\nprompt = "{d}"\n'


def make(make_kit, text="v1"):
    return make_kit({
        "skills/s/SKILL.md": SKILL.format(name="s", body=text),
        "agents/reviewer-x.toml": AGENT.format(d=text),
    })


def add(project, user_dir, repo, **options):
    return kit_ops.install(
        project, user_dir, str(repo.path), output=lambda _: None, **options
    )


def agent_text(project):
    return load_config(project)["customizations"].agent("reviewer-x").description


def start_run(project):
    store = RunStore(project)
    record = store.create(
        source="cli", request="r", repo=project, worktree=project, branch="main"
    )
    return store, record


def prune_like_another_project(user_dir, sha):
    """Another project's cleanup cannot know this project's run needs `sha`."""
    for folder in kits_home(user_dir).glob(f"*/{sha}"):
        shutil.rmtree(folder)
    assert not list(kits_home(user_dir).glob(f"*/{sha}"))


def forget_pins(monkeypatch):
    """What a brand new process (another Claude Code session) sees."""
    monkeypatch.setenv(PINS_ENV, "")


def test_runs_without_kits_record_and_export_nothing(project, user_dir):
    _store, record = start_run(project)
    assert record.kit_pins == {}
    assert os.environ[PINS_ENV] == ""


def test_run_records_its_kit_commits_and_exports_them(make_kit, project, user_dir):
    repo = make(make_kit)
    spec = add(project, user_dir, repo)
    _store, record = start_run(project)
    sha = read_lock(user_dir / "kits.lock")[spec.name]["sha"]
    assert record.kit_pins == {
        f"user/{spec.name}": {
            "url": normalize_url(str(repo.path)),
            "sha": sha,
            "hooks_sha": sha,
        }
    }
    assert json.loads(os.environ[PINS_ENV]) == record.kit_pins


def test_kit_update_during_a_run_does_not_change_the_run(
    make_kit, project, user_dir, monkeypatch
):
    repo = make(make_kit)
    add(project, user_dir, repo)
    start_run(project)
    repo.write({"agents/reviewer-x.toml": AGENT.format(d="v2")})
    kit_ops.update(project, user_dir)  # another session updates the kit
    assert agent_text(project) == "v1"  # the run, and its workers, still see v1
    forget_pins(monkeypatch)
    assert agent_text(project) == "v2"  # everyone else sees the update


def test_resume_applies_recorded_pins_and_says_so(
    make_kit, project, user_dir, monkeypatch
):
    repo = make(make_kit)
    spec = add(project, user_dir, repo)
    _store, record = start_run(project)
    repo.write({"agents/reviewer-x.toml": AGENT.format(d="v2")})
    kit_ops.update(project, user_dir)
    forget_pins(monkeypatch)
    old = record.kit_pins[f"user/{spec.name}"]["sha"]
    new = read_lock(user_dir / "kits.lock")[spec.name]["sha"]
    with pinned(record.kit_pins, project, user_dir) as notes:
        assert agent_text(project) == "v1"
        assert notes == [
            f"kit {spec.name}: using {old[:7]}, the version this run started "
            f"with (installed: {new[:7]})"
        ]
    assert os.environ[PINS_ENV] == ""  # restored for the next run in this process
    assert agent_text(project) == "v2"


def test_resume_redownloads_a_pruned_commit(make_kit, project, user_dir, monkeypatch):
    repo = make(make_kit)
    add(project, user_dir, repo)
    _store, record = start_run(project)
    repo.write({"agents/reviewer-x.toml": AGENT.format(d="v2")})
    kit_ops.update(project, user_dir)
    forget_pins(monkeypatch)
    pin = next(iter(record.kit_pins.values()))
    prune_like_another_project(user_dir, pin["sha"])
    with pinned(record.kit_pins, project, user_dir) as notes:
        assert agent_text(project) == "v1"
        assert len(notes) == 1
    assert list(kits_home(user_dir).glob(f"*/{pin['sha']}"))


def test_unrestorable_pin_falls_back_with_a_warning(
    make_kit, project, user_dir, monkeypatch
):
    repo = make(make_kit)
    add(project, user_dir, repo)
    _store, record = start_run(project)
    repo.write({"agents/reviewer-x.toml": AGENT.format(d="v2")})
    kit_ops.update(project, user_dir)
    forget_pins(monkeypatch)
    prune_like_another_project(user_dir, next(iter(record.kit_pins.values()))["sha"])
    repo.path.rename(repo.path.with_name("gone"))  # the remote is unreachable
    with pinned(record.kit_pins, project, user_dir) as notes:
        assert agent_text(project) == "v2"
        assert "could not restore" in notes[0] and "installed version" in notes[0]


def test_kit_removed_since_the_run_started_is_reported(
    make_kit, project, user_dir, monkeypatch
):
    spec = add(project, user_dir, make(make_kit))
    _store, record = start_run(project)
    kit_ops.uninstall(project, user_dir, spec.name)
    forget_pins(monkeypatch)
    with pinned(record.kit_pins, project, user_dir) as notes:
        assert "installed differently now" in notes[0]


def test_garbage_in_the_environment_is_ignored(
    make_kit, project, user_dir, monkeypatch
):
    add(project, user_dir, make(make_kit))
    for junk in ("not json", "[]", '{"user/x": 3}', '{"user/x": {"sha": 5}}'):
        monkeypatch.setenv(PINS_ENV, junk)
        assert agent_text(project) == "v1"


def test_unfinished_runs_protect_their_commits_from_pruning(
    make_kit, project, user_dir, monkeypatch
):
    repo = make(make_kit)
    spec = add(project, user_dir, repo)
    store, record = start_run(project)
    old = next(iter(record.kit_pins.values()))["sha"]
    repo.write({"agents/reviewer-x.toml": AGENT.format(d="v2")})
    kit_ops.update(project, user_dir)
    assert list(kits_home(user_dir).glob(f"*/{old}"))  # a live run still needs it
    assert old in active_run_shas(project, normalize_url(spec.url))
    path = store.directory / f"{record.id}.json"
    data = json.loads(path.read_text())
    data["phase"] = "delivered"
    path.write_text(json.dumps(data))
    kit_ops.update(project, user_dir)
    assert not list(kits_home(user_dir).glob(f"*/{old}"))  # finished: free to go


def test_resume_wraps_only_a_continued_run_and_restores_the_environment(
    make_kit, project, user_dir, monkeypatch
):
    add(project, user_dir, make(make_kit))
    _store, record = start_run(project)
    forget_pins(monkeypatch)
    seen = {}

    async def fake(working_dir, run_id, **options):
        await asyncio.sleep(0)
        seen[options["continue_run"]] = os.environ.get(PINS_ENV)
        return 0

    monkeypatch.setattr(resume_cli, "_resume_run", fake)
    for continued in (True, False):
        code = asyncio.run(
            resume_cli.resume(project, record.id, continue_run=continued)
        )
        assert code == 0
    assert json.loads(seen[True]) == record.kit_pins  # continuing: pinned
    assert seen[False] == ""  # only inspecting: nothing applied
    assert os.environ[PINS_ENV] == ""


def test_resume_of_an_unknown_run_is_left_to_the_normal_error(project, user_dir):
    assert resume_cli._recorded_pins(project, "0" * 32) == {}


@pytest.mark.parametrize("phase", ["parked", "reviewer_started"])
def test_parked_and_running_runs_count_as_unfinished(
    make_kit, project, user_dir, phase
):
    spec = add(project, user_dir, make(make_kit))
    store, record = start_run(project)
    path = store.directory / f"{record.id}.json"
    data = json.loads(path.read_text())
    data["phase"] = phase
    path.write_text(json.dumps(data))
    assert active_run_shas(project, normalize_url(spec.url))

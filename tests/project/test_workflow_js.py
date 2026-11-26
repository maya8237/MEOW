"""Workflows as Claude Code workflow scripts, and moving off TOML."""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from meow.execution.run_state import RunStore
from meow.native.native_custom import stage
from meow.project.config import load_config
from meow.project.custom import builtin_dir
from meow.project.custom.diagnostics import drift_message
from meow.project.custom.registration import old_layout
from meow.project.custom.workflow_js import read_workflow_js, to_js
from meow.project.custom.workflows import fingerprint, select_workflow

EDITED_ROUNDS = 5
FIXTURES = Path(__file__).parent / "fixtures"
NIGHTLY = """\
description = "Nightly audit"
short_plan = true

[[steps]]
id = "plan"
uses = "builtin.plan"

[[steps]]
id = "rounds"
max_rounds = 2
loop = [
  { id = "generate", uses = "builtin.generate", when = "round > 1 or feedback" },
  { id = "review", uses = "builtin.review" },
]
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    (user_home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    return user_home


@pytest.fixture
def project(tmp_path, home):
    root = tmp_path / "project"
    (root / ".meow" / "workflows").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    return root


def _catalog(project):
    return load_config(project)["customizations"]


def test_every_builtin_is_a_script_that_round_trips():
    for path in sorted((builtin_dir() / "workflows").glob("*.js")):
        data = read_workflow_js(path)
        name = data.pop("name")
        assert to_js(name, data) == path.read_text(encoding="utf-8")
    assert not list((builtin_dir() / "workflows").glob("*.toml"))


def test_the_plugin_ships_only_the_default_workflow():
    root = Path(__file__).resolve().parents[2]
    shipped = sorted(path.name for path in (root / "workflows").iterdir())
    assert shipped == ["default.js"]
    assert (root / "workflows" / "default.js").read_bytes() == (
        builtin_dir() / "workflows" / "default.js"
    ).read_bytes()


def test_a_handwritten_toml_workflow_migrates_to_the_same_definition(project):
    path = project / ".meow" / "workflows" / "nightly.toml"
    path.write_text(NIGHTLY, encoding="utf-8")
    before = _catalog(project).workflow("nightly")
    before_print = fingerprint(before, _catalog(project))

    assert old_layout(project, apply=True) == [f"migrated {path} -> nightly.js"]

    after = _catalog(project).workflow("nightly")
    assert after.file == path.with_suffix(".js")
    assert (after.description, after.steps, after.short_plan) == (
        before.description,
        before.steps,
        before.short_plan,
    )
    assert fingerprint(after, _catalog(project)) == before_print


def test_a_never_edited_eject_from_0_1_42_is_brought_up_to_date(project):
    path = project / ".meow" / "workflows" / "quick.toml"
    shutil.copy(FIXTURES / "ejected_quick_0_1_42.toml", path)

    assert old_layout(project, apply=True) == [
        f"migrated {path} -> quick.js (never edited: updated to the shipped version)"
    ]
    workflow = _catalog(project).workflow("quick")
    assert workflow.source.scope == "project"
    assert drift_message(workflow, "workflows") is None


def test_an_edited_eject_keeps_its_text_and_reports_drift(project):
    text = (FIXTURES / "ejected_quick_0_1_42.toml").read_text(encoding="utf-8")
    path = project / ".meow" / "workflows" / "quick.toml"
    path.write_text(text.replace("max_rounds = 3", "max_rounds = 5"), encoding="utf-8")

    old_layout(project, apply=True)

    workflow = _catalog(project).workflow("quick")
    assert workflow.steps[2].max_rounds == EDITED_ROUNDS
    assert "changed" in drift_message(workflow, "workflows")


def test_a_run_recorded_before_the_upgrade_still_resumes(project):
    """Older releases fingerprinted file bytes, which migration rewrites."""
    path = project / ".meow" / "workflows" / "nightly.toml"
    path.write_text(NIGHTLY, encoding="utf-8")
    saved = {"name": "nightly", "fingerprint": "0" * 64}

    old_layout(project, apply=True)

    assert select_workflow(load_config(project), saved=saved).name == "nightly"


def test_a_changed_workflow_still_refuses_a_current_fingerprint(project):
    path = project / ".meow" / "workflows" / "nightly.js"
    path.write_text(to_js("nightly", tomllib.loads(NIGHTLY)), encoding="utf-8")
    config = load_config(project)
    saved = {
        "name": "nightly",
        "fingerprint": fingerprint(
            config["customizations"].workflow("nightly"), config["customizations"]
        ),
    }
    path.write_text(
        path.read_text().replace("Nightly audit", "Changed"), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Workflow changed"):
        select_workflow(load_config(project), saved=saved)


def test_whitespace_and_comments_do_not_change_the_fingerprint(project):
    path = project / ".meow" / "workflows" / "nightly.js"
    path.write_text(to_js("nightly", tomllib.loads(NIGHTLY)), encoding="utf-8")
    before = fingerprint(_catalog(project).workflow("nightly"), _catalog(project))
    path.write_text(path.read_text() + "\n// a note\n", encoding="utf-8")

    assert (
        fingerprint(_catalog(project).workflow("nightly"), _catalog(project)) == before
    )


@pytest.mark.parametrize(
    "text, message",
    [
        ("const x = 1\n", "export const meta"),
        ('export const meta = {"name": "w", "description": "d"}\n', "definition"),
        (
            'export const meta = {"name": "w", "description": "d"}\nconst NAME = "x"\n'
            'const MEOW = /*meow:workflow*/ {"steps": []} /*meow:end*/\n',
            "repeat",
        ),
        (
            'export const meta = {"name": "w", "description": "d", "x": 1}\n'
            'const MEOW = /*meow:workflow*/ {"steps": []} /*meow:end*/\n',
            "only name",
        ),
        (
            'export const meta = {"name": "w", "description": "d"}\n'
            "const MEOW = /*meow:workflow*/ {steps: []} /*meow:end*/\n",
            "JSON",
        ),
    ],
)
def test_malformed_scripts_name_the_file(project, text, message):
    path = project / ".meow" / "workflows" / "w.js"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=message) as error:
        load_config(project)
    assert "w.js" in str(error.value)


def test_stage_renders_a_gate_like_meow_run(project):
    (project / ".meow" / "config.toml").write_text("", encoding="utf-8")
    record = RunStore(project).create(
        source="prompt",
        request="Add CSV export",
        repo=project,
        worktree=project,
        branch="main",
    )
    plan = project / ".meow" / "plans" / "csv.md"

    out = stage(
        project,
        project,
        "plan-checker",
        workflow_name="default",
        run_id=record.id,
        plan=plan,
    )

    assert out["kind"] == "gate"
    assert out["report_file"].endswith("csv.plan-checker.stage.md")
    assert (
        str(plan) in out["system_prompt"] and "Add CSV export" in out["system_prompt"]
    )
    assert "STATUS: PASS" in out["system_prompt"]
    assert out["report_file"] in out["task"]


def test_stage_refuses_a_builtin_step(project):
    (project / ".meow" / "config.toml").write_text("", encoding="utf-8")
    record = RunStore(project).create(
        source="prompt", request="x", repo=project, worktree=project, branch="main"
    )

    with pytest.raises(ValueError, match="no agent or gate step 'plan'"):
        stage(project, project, "plan", workflow_name="default", run_id=record.id)

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

from meow.native.native_prepare import verdict
from meow.native.native_prompt import role_prompt
from tests.native.test_native import make_repo


@pytest.mark.parametrize("scope", ["diff", "project"])
def test_native_local_verdict_rejects_deleted_check_binding(tmp_path, scope):
    root = make_repo(str(tmp_path))
    prompt = role_prompt(root, root, "reviewer-prompt", scope=scope)
    report = root / prompt["review_file"]
    report.write_text("SUMMARY: good\nSTATUS: PASS\n")
    report.with_suffix(report.suffix + ".checks.json").unlink()
    result = verdict(report)
    assert result["status"] == "FAIL"
    assert "binding" in " ".join(result["gate_errors"])


@pytest.mark.parametrize(
    "name",
    [
        "gitlab-review.md",
        "github-review.md",
        "gitlab.1234abcd.review.md",
        "github.1234abcd.review.md",
    ],
)
def test_remote_verdict_requires_no_local_verification_binding(tmp_path, name):
    report = tmp_path / name
    report.write_text("SUMMARY: remote diff reviewed\nSTATUS: PASS\n")
    assert verdict(report)["status"] == "PASS"


@pytest.mark.parametrize(
    "name",
    ["feature-review.md", "branch.1234abcd.review.md", "review.md", "arbitrary.md"],
)
def test_unprepared_local_report_cannot_pass_without_binding(tmp_path, name):
    report = tmp_path / name
    report.write_text("SUMMARY: unbound\nSTATUS: PASS\n")
    result = verdict(report)
    assert result["status"] == "FAIL"
    assert "binding" in " ".join(result["gate_errors"])


def test_native_project_review_cannot_pass_without_frontend_coverage(tmp_path):
    root = make_repo(str(tmp_path))
    (root / "frontend").mkdir()
    (root / "frontend/app.js").write_text("console.log('app');\n")
    prompt = role_prompt(root, root, "reviewer-prompt", scope="project", host="codex")
    report = root / prompt["review_file"]
    report.write_text("SUMMARY: no bugs\nSTATUS: PASS\n")
    result = verdict(report)
    assert result["status"] == "FAIL"
    assert "frontend" in " ".join(result["gate_errors"])
    assert "STATUS: FAIL" in report.read_text()
    report.with_suffix(report.suffix + ".scope.json").unlink()
    report.write_text("SUMMARY: no bugs\nSTATUS: PASS\n")
    assert verdict(report)["status"] == "FAIL"


def test_native_required_checks_cannot_be_skipped_or_reused_after_edit(tmp_path):
    from meow.infrastructure.review_verification import review_evidence
    from meow.project.config import load_config

    root = make_repo(str(tmp_path))
    with (root / ".meow/config.toml").open("a") as stream:
        stream.write(
            "\n[[build]]\ncommand = "
            + json.dumps(sys.executable)
            + '\nargs = ["-c", "print(123)"]\n'
        )
    prompt = role_prompt(root, root, "reviewer-prompt", scope="diff")
    report = root / prompt["review_file"]
    report.write_text("SUMMARY: no bugs\nSTATUS: PASS\n")
    assert verdict(report)["status"] == "FAIL"
    asyncio.run(review_evidence(root, load_config(root)))
    report.write_text("SUMMARY: no bugs\nSTATUS: PASS\n")
    assert verdict(report)["status"] == "PASS"
    (root / "new.py").write_text("print('changed')")
    assert verdict(report)["status"] == "FAIL"


def test_native_cli_passes_host_capabilities_and_scope(tmp_path):
    from tests.native.test_native_cli import run_native

    with patch("meow.native.native.role_prompt", return_value={}) as prompt:
        code, out, err = run_native(
            "prompt",
            "reviewer-prompt",
            "--scope",
            "project",
            "--host",
            "codex",
            "--available-model",
            "gpt-test",
            "--work-dir",
            str(tmp_path),
        )
    assert code == 0, err
    assert json.loads(out) == {}
    assert prompt.call_args.kwargs["host"] == "codex"
    assert prompt.call_args.kwargs["scope"] == "project"
    assert prompt.call_args.kwargs["available_models"] == ["gpt-test"]


def test_native_plan_report_cannot_pass_after_runtime_plan_changes(tmp_path):
    root = make_repo(str(tmp_path))
    (root / ".meow/config.toml").write_text('docs_dir = ".meow/plans"\n')
    plan = root / ".meow/plans/feature.md"
    plan.parent.mkdir()
    plan.write_text("# Original acceptance criteria\n")
    prompt = role_prompt(root, root, "reviewer-plan", plan_file=plan)
    report = root / prompt["review_file"]
    report.write_text("SUMMARY: met\nSTATUS: PASS\n")
    assert verdict(report)["status"] == "PASS"
    plan.write_text("# New acceptance criteria never reviewed\n")
    assert verdict(report)["status"] == "FAIL"


def test_native_report_binding_cannot_be_substituted_at_another_path(tmp_path):
    root = make_repo(str(tmp_path))
    (root / ".meow/config.toml").write_text('docs_dir = ".meow/plans"\n')
    prompt = role_prompt(root, root, "reviewer-prompt", scope="diff")
    report = root / prompt["review_file"]
    report.write_text("SUMMARY: met\nSTATUS: PASS\n")
    other = report.with_name("unrelated-review.md")
    other.write_text(report.read_text())
    other.with_suffix(other.suffix + ".checks.json").write_text(
        report.with_suffix(report.suffix + ".checks.json").read_text()
    )
    assert verdict(other)["status"] == "FAIL"


def test_preparing_next_native_review_cannot_rebind_an_old_pass(tmp_path):
    root = make_repo(str(tmp_path))
    (root / ".meow/config.toml").write_text('docs_dir = ".meow/plans"\n')
    plan = root / ".meow/plans/feature.md"
    plan.parent.mkdir()
    plan.write_text("# Acceptance criteria\n")
    prompt = role_prompt(root, root, "reviewer-plan", plan_file=plan)
    report = root / prompt["review_file"]
    report.write_text("SUMMARY: old review\nSTATUS: PASS\n")
    assert verdict(report)["status"] == "PASS"
    role_prompt(root, root, "reviewer-plan", plan_file=plan)
    assert verdict(report)["status"] == "FAIL"

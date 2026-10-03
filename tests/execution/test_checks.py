"""Gate freshness and build execution."""

import asyncio
import subprocess
import sys
from unittest.mock import patch

import pytest

from meow.execution.run_state import MAX_OUTPUT
from meow.infrastructure.checks import (
    Check,
    checks_current,
    code_revision,
    completion_ready,
    config_fingerprint,
    preflight_check,
    run_check,
    run_final_checks,
)
from meow.project.config import load_config


def test_preflight_validates_executable_and_cwd_without_running(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    check = Check(
        "build",
        "compile",
        sys.executable,
        ("-c", "print('ok')"),
        cwd=__import__("pathlib").Path("app"),
    )
    result = preflight_check(tmp_path, check)
    assert result.status == "ready_unchecked"
    assert result.cwd == app
    assert not (app / "side-effect").exists()


def test_preflight_reports_missing_executable_and_cwd(tmp_path):
    missing = Check("lint", "missing", "not-a-real-meow-command")
    assert preflight_check(tmp_path, missing).status == "missing_executable"
    wrong_dir = Check(
        "test", "bad-cwd", sys.executable, cwd=__import__("pathlib").Path("missing")
    )
    assert preflight_check(tmp_path, wrong_dir).status == "invalid_cwd"


def test_executed_preflight_reports_timeout_and_file_mutation(tmp_path):
    timeout = Check(
        "test",
        "slow",
        sys.executable,
        ("-c", "import time; time.sleep(2)"),
        timeout=0.1,
    )
    assert preflight_check(tmp_path, timeout, execute=True).status == "timed_out"
    file = tmp_path / "source.py"
    file.write_text("before")
    edit = Check(
        "build", "edit", sys.executable, ("-c", "open('source.py','w').write('after')")
    )
    result = preflight_check(tmp_path, edit, execute=True)
    assert result.status == "changed_files"
    assert "source.py" in result.changed_files


def test_revision_and_config_changes_invalidate_evidence(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    file = tmp_path / "source.py"
    file.write_text("one", encoding="utf-8")
    config = tmp_path / ".harness.toml"
    config.write_text("x=1", encoding="utf-8")
    check = Check(
        "build",
        "compile",
        sys.executable,
        (
            "-c",
            "print('ok')",
        ),
    )
    result = run_check(tmp_path, check)
    assert result.passed
    assert checks_current([result], [check], tmp_path)
    file.write_text("two", encoding="utf-8")
    assert code_revision(tmp_path) != result.revision
    assert not checks_current([result], [check], tmp_path)
    file.write_text("one", encoding="utf-8")
    config.write_text("x=2", encoding="utf-8")
    assert config_fingerprint(tmp_path) != result.config_fingerprint
    assert not checks_current([result], [check], tmp_path)


def test_required_failure_blocks_but_advisory_failure_does_not(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    failing = Check(
        "build", "fail", sys.executable, ("-c", "raise SystemExit(2)"), required=False
    )
    result = run_check(tmp_path, failing)
    assert result.exit_code == int("2")
    assert checks_current([result], [failing], tmp_path)
    required = Check("build", "fail", failing.command, failing.args, required=True)
    assert not checks_current([result], [required], tmp_path)


def test_same_command_with_different_args_cannot_share_evidence(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    passing = Check("build", "first", sys.executable, ("-c", "pass"))
    failing = Check("build", "second", sys.executable, ("-c", "raise SystemExit(2)"))
    result = run_check(tmp_path, passing)
    assert not checks_current([result], [passing, failing], tmp_path)


def test_output_is_bounded(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    check = Check(
        "build",
        "large",
        sys.executable,
        (
            "-c",
            "print('x'*10000)",
        ),
    )
    assert len(run_check(tmp_path, check).output) <= MAX_OUTPUT


def test_build_defaults_and_advisory_config(tmp_path):
    (tmp_path / ".harness.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n'
        '[[build]]\ncommand="python -m compileall src"\nrequired=false\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config["build"][0].kind == "build"
    assert not config["build"][0].required


def test_build_rejects_invalid_command(tmp_path):
    (tmp_path / ".harness.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n[[build]]\ncommand=""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="build"):
        load_config(tmp_path)


def test_completion_requires_review_tester_and_current_required_gates(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    check = Check(
        "build",
        "ok",
        sys.executable,
        (
            "-c",
            "print('ok')",
        ),
    )
    result = run_check(tmp_path, check)
    assert completion_ready([result], [check], tmp_path, "PASS", "PASS")
    assert not completion_ready([result], [check], tmp_path, "FAIL", "PASS")
    assert not completion_ready([result], [check], tmp_path, "PASS", "FAIL")
    (tmp_path / "edit.py").write_text("changed", encoding="utf-8")
    assert not completion_ready([result], [check], tmp_path, "PASS", "PASS")


def test_completion_rejects_stale_reviewer_evidence(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    check = Check("build", "ok", sys.executable, ("-c", "pass"))
    reviewed_revision = code_revision(tmp_path)
    (tmp_path / "source.py").write_text("new edit", encoding="utf-8")
    result = run_check(tmp_path, check)
    assert not completion_ready(
        [result],
        [check],
        tmp_path,
        "PASS",
        reviewer_revision=reviewed_revision,
    )


def test_final_test_check_uses_integrated_stage(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    (tmp_path / "sample.py").write_text("print('ok')", encoding="utf-8")
    from meow.project.config import TestCommand

    config = {
        "lint": [],
        "lint_timeout": 60,
        "build": [],
        "tester": {
            "tests": [
                TestCommand(
                    tmp_path.relative_to(tmp_path), sys.executable, ("sample.py",)
                )
            ],
            "dev_server": [],
        },
    }
    results = asyncio.run(run_final_checks(tmp_path, config))
    assert len(results) == 1
    assert results[0].kind == "test" and results[0].passed


def test_final_checks_rerun_when_build_changes_code(tmp_path):
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    (tmp_path / "source.py").write_text("old", encoding="utf-8")
    lint = Check("lint", "lint", sys.executable, ("-c", "pass"))
    build = Check(
        "build",
        "build",
        sys.executable,
        (
            "-c",
            "from pathlib import Path; p=Path('source.py'); "
            "p.write_text('done') if p.read_text() != 'done' else None",
        ),
    )
    config = {"lint": [], "build": [], "tester": {"tests": []}}
    with patch(
        "meow.infrastructure.checks.configured_checks", return_value=[lint, build]
    ):
        results = asyncio.run(run_final_checks(tmp_path, config))
    assert len(results) == len([lint, build])
    assert checks_current(results, [lint, build], tmp_path)

import asyncio
import json
import sys
from unittest.mock import patch

from meow.native.native_prepare import verdict
from meow.native.native_prompt import role_prompt
from tests.native.test_native import make_repo


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

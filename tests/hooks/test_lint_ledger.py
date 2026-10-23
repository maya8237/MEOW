import asyncio
import json
import subprocess
import sys
from dataclasses import replace

import pytest

from meow.hooks.claude import install_claude_hooks
from meow.infrastructure import lint_ledger
from meow.infrastructure.checks import run_final_checks
from meow.infrastructure.lint import make_lint_hook
from meow.project.config import load_config
from meow.project.config_models import LintCommand


def project(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text("", encoding="utf-8")
    source = tmp_path / "code.py"
    source.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "code.py"], cwd=tmp_path, check=True)
    config = load_config(tmp_path)
    # Whole-project execution would fail; the file hook supplies the last argument.
    config["lint"] = [
        LintCommand(
            sys.executable,
            args=("-c", "import sys; assert len(sys.argv) == 2"),
            include=(source.relative_to(tmp_path),),
        )
    ]
    install_claude_hooks(tmp_path, ["lint"])
    return source, config


def hook(tmp_path, source, config):
    callback = make_lint_hook(tmp_path, config["lint"], config["lint_timeout"])
    return asyncio.run(
        callback(
            {"tool_name": "Edit", "tool_input": {"file_path": str(source)}}, None, None
        )
    )


def test_sdk_hook_proves_lint_and_verify_reuses_it(tmp_path):
    source, config = project(tmp_path)
    assert hook(tmp_path, source, config) == {}
    evidence = lint_ledger.verified_lint(tmp_path, config)
    assert evidence
    results = asyncio.run(run_final_checks(tmp_path, config, hook_lint=True))
    assert results[0].passed
    assert "hook ledger" in results[0].output
    # A full final check does not trust the hook by itself.
    assert not asyncio.run(run_final_checks(tmp_path, config))[0].passed


@pytest.mark.parametrize(
    "gap",
    [
        "content",
        "config",
        "missing",
        "malformed",
        "inactive",
        "global",
        "failed",
        "deleted",
    ],
)
def test_uncertain_hook_evidence_falls_back(tmp_path, gap):  # ruff: ignore[complex-structure, too-many-branches, too-many-statements] -- independently corrupt each trust prerequisite
    source, config = project(tmp_path)
    hook(tmp_path, source, config)
    ledger = tmp_path / ".meow/lint-ledger.jsonl"
    if gap == "content":
        source.write_text("x = 2\n", encoding="utf-8")
    elif gap == "config":
        config["lint"] = [replace(config["lint"][0], timeout=1)]
    elif gap == "missing":
        ledger.unlink()
    elif gap == "malformed":
        ledger.write_text("bad json\n", encoding="utf-8")
    elif gap == "inactive":
        (tmp_path / ".claude/settings.local.json").write_text("{}", encoding="utf-8")
    elif gap == "global":
        config["lint"] = [replace(config["lint"][0], per_file=False)]
    elif gap == "failed":
        entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        entry["ok"] = False
        ledger.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    else:
        source.unlink()
        subprocess.run(
            ["git", "add", "-N", "code.py"], cwd=tmp_path, capture_output=True
        )
    assert lint_ledger.verified_lint(tmp_path, config) is None


def test_sdk_only_evidence_is_recorded_but_requires_active_installation(tmp_path):
    source, config = project(tmp_path)
    (tmp_path / ".claude/settings.local.json").write_text("{}", encoding="utf-8")
    hook(tmp_path, source, config)
    assert (tmp_path / ".meow/lint-ledger.jsonl").exists()
    assert lint_ledger.verified_lint(tmp_path, config) is None

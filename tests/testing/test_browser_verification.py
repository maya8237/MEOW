import asyncio
import socket
import sys
from pathlib import Path

from meow.execution.orchestrator import _tester_results
from meow.infrastructure.checks import configured_checks, run_final_checks
from meow.infrastructure.test_runner import (
    BrowserEvidence,
    _reachable,
    prepared_test_stage,
)
from meow.infrastructure.test_runner import (
    VerificationStageEvidence as StageEvidence,
)
from meow.project.config import load_config
from meow.project.config_models import DevServerCommand


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _browser_config(tmp_path, *, exit_code=0):
    port = _port()
    artifact = tmp_path / ".meow" / "browser-proof.txt"
    artifact.parent.mkdir(exist_ok=True)
    script = tmp_path / "browser_flow.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(artifact)!r}).write_text('flow checked')\n"
        f"raise SystemExit({exit_code})\n"
    )
    return {
        "tests": [],
        "dev_server": [
            DevServerCommand(
                Path("."),
                sys.executable,
                ("-m", "http.server", str(port), "--bind", "127.0.0.1"),
                None,
                f"http://127.0.0.1:{port}",
                5,
            )
        ],
        "browser": {
            "kind": "command",
            "name": "project-browser",
            "entrypoint": sys.executable,
            "args": [str(script)],
            "cwd": ".",
            "timeout": 5,
            "required": True,
            "flows": ["home page"],
            "artifacts": [".meow/browser-proof.txt"],
        },
    }


def test_browser_command_runs_with_server_and_captures_artifacts(tmp_path):
    config = {"tester": _browser_config(tmp_path)}

    async def run():
        async with prepared_test_stage(tmp_path, config) as evidence:
            assert evidence.browser[0].status == "passed"
            assert evidence.browser[0].flows == ("home page",)
            assert evidence.browser[0].artifacts == (".meow/browser-proof.txt",)
            assert not evidence.blocking_failed

    asyncio.run(run())


def test_required_browser_without_start_command_is_unavailable(tmp_path):
    browser = _browser_config(tmp_path)["browser"]
    config = {"tester": {"tests": [], "dev_server": [], "browser": browser}}

    async def run():
        async with prepared_test_stage(tmp_path, config) as evidence:
            assert evidence.browser[0].status == "unavailable"
            assert evidence.blocking_failed

    asyncio.run(run())


def test_browser_provider_failure_blocks_final_check(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    browser = _browser_config(tmp_path, exit_code=2)
    config = {"lint": [], "build": [], "tester": browser}
    checks = configured_checks(config)
    assert any(item.kind == "browser" for item in checks)
    results = asyncio.run(run_final_checks(tmp_path, config))
    outcome = next(item for item in results if item.kind == "browser")
    assert not outcome.passed
    assert outcome.required
    assert "browser-proof.txt" in outcome.output
    assert not _reachable(browser["dev_server"][0].ready_url)


def test_config_accepts_command_browser_provider(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n'
        '[tester.browser]\nkind="command"\nname="project-browser"\n'
        'entrypoint="npx playwright test"\nrequired=true\n'
        'flows=["home page"]\nartifacts=["test-results/home.png"]\n'
    )
    browser = load_config(tmp_path)["tester"]["browser"]
    assert browser["kind"] == "command"
    assert browser["required"]


def test_browser_evidence_is_preserved_in_run_results():
    evidence = StageEvidence(
        browser=(
            BrowserEvidence(
                "project-browser",
                "command",
                "passed",
                required=True,
                flows=("home page",),
                artifacts=(".meow/home.png",),
                exit_code=0,
            ),
        )
    )
    results = _tester_results("PASS", "PASS", evidence)
    assert results["browser"]["status"] == "passed"
    assert results["browser"]["artifacts"] == [".meow/home.png"]

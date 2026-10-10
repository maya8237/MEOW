import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from meow.agents.docs_updater import update_documentation
from meow.cli import cli
from meow.integrations.docs_update import (
    DocsUpdateError,
    prepare_docs_update,
    run_docs_update,
)
from meow.project.permissions import PermissionPolicy, Rule


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "dev")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CLI.md").write_text("# CLI\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initial")
    first = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "src" / "app.py").write_text("VALUE = 2\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "change")
    return tmp_path, first


def test_first_use_requires_explicit_baseline(repo):
    root, _ = repo
    with pytest.raises(DocsUpdateError, match="--since"):
        prepare_docs_update(root, None)
    assert not (root / "docs" / ".meow-docs-update.json").exists()


def test_preflight_reports_commits_and_changes(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)
    assert prepared.baseline_sha == first
    assert prepared.head_sha == git(root, "rev-parse", "HEAD")
    assert "src/app.py" in prepared.changed_paths
    assert "+VALUE = 2" in prepared.diff


def test_preflight_refuses_dirty_wrong_branch_and_invalid_refs(repo):
    root, first = repo
    (root / "scratch").write_text("dirty")
    with pytest.raises(DocsUpdateError, match="clean"):
        prepare_docs_update(root, first)
    (root / "scratch").unlink()
    git(root, "switch", "-c", "other")
    with pytest.raises(DocsUpdateError, match="dev"):
        prepare_docs_update(root, first)
    git(root, "switch", "dev")
    with pytest.raises(DocsUpdateError, match="baseline"):
        prepare_docs_update(root, "missing-ref")


def test_saved_marker_is_used_only_after_commit(repo):
    root, first = repo
    marker = root / "docs" / ".meow-docs-update.json"
    marker.write_text(
        json.dumps({
            "baseline_sha": first,
            "inspected_head": git(root, "rev-parse", "HEAD"),
        })
    )
    with pytest.raises(DocsUpdateError, match="clean"):
        prepare_docs_update(root, None)
    git(root, "add", ".")
    git(root, "commit", "-m", "docs baseline")
    assert (
        prepare_docs_update(root, None).baseline_sha
        == json.loads(marker.read_text())["inspected_head"]
    )


def test_nonancestor_baseline_is_refused(repo):
    root, _ = repo
    git(root, "switch", "--orphan", "unrelated")
    (root / "unrelated.txt").write_text("other\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "unrelated")
    unrelated = git(root, "rev-parse", "HEAD")
    git(root, "switch", "dev")
    with pytest.raises(DocsUpdateError, match="ancestor"):
        prepare_docs_update(root, unrelated)


def test_update_rejects_code_edits_and_does_not_advance_marker(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)

    async def bad_agent(_):  # ruff: ignore[unused-async] -- async adapter contract
        (root / "src" / "app.py").write_text("VALUE = 3\n")

    with pytest.raises(DocsUpdateError, match="outside documentation"):
        asyncio.run(run_docs_update(prepared, bad_agent))
    assert not (root / "docs" / ".meow-docs-update.json").exists()


def test_update_writes_marker_and_reviewable_diff(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)

    async def agent(_):  # ruff: ignore[unused-async] -- async adapter contract
        (root / "docs" / "CLI.md").write_text("# CLI\nVALUE is two.\n")

    result = asyncio.run(run_docs_update(prepared, agent))
    assert "docs/CLI.md" in result.changed_paths
    assert "+VALUE is two." in result.diff
    marker = json.loads((root / "docs" / ".meow-docs-update.json").read_text())
    assert marker["inspected_head"] == prepared.head_sha


def test_failed_agent_does_not_write_baseline_marker(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)

    async def broken(_):  # ruff: ignore[unused-async] -- async adapter contract
        raise RuntimeError("SDK failed")

    with pytest.raises(RuntimeError, match="SDK failed"):
        asyncio.run(run_docs_update(prepared, broken))
    assert not (root / "docs" / ".meow-docs-update.json").exists()


def test_new_document_appears_in_reviewable_diff(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)

    async def agent(_):  # ruff: ignore[unused-async] -- async adapter contract
        (root / "docs" / "new.md").write_text("# New behavior\n")

    result = asyncio.run(run_docs_update(prepared, agent))
    assert "docs/new.md" in result.diff
    assert "+# New behavior" in result.diff


def test_cli_exposes_manual_docs_update_and_reports_preflight_error(repo, capsys):
    root, _ = repo
    parser = cli._build_arg_parser()
    args = parser.parse_args(["docs-update", "-d", str(root)])
    assert args.since is None
    with (
        patch("sys.argv", ["meow", "docs-update", "-d", str(root)]),
        pytest.raises(SystemExit) as exc,
    ):
        cli.cli_main()
    assert exc.value.code != 0
    assert "--since" in capsys.readouterr().err


def test_docs_updater_uses_project_permission_callback(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)
    config = {
        "models": {"reviewer": None},
        "lint": [],
        "permissions": PermissionPolicy((Rule("docs_updater", "Write", "deny"),)),
    }
    received = []

    async def capture(_prompt, options, _role):  # ruff: ignore[unused-async]
        received.append(options)

    with (
        patch("meow.agents.docs_updater.load_config", return_value=config),
        patch("meow.agents.docs_updater.Agent.run_query", side_effect=capture),
    ):
        asyncio.run(update_documentation(prepared))
    assert received[0].can_use_tool is not None


def test_docs_updater_blocks_source_edits_before_sdk_tool_call(repo):
    root, first = repo
    prepared = prepare_docs_update(root, first)
    config = {"models": {"reviewer": None}, "lint": []}
    received = []

    async def capture(_prompt, options, _role):  # ruff: ignore[unused-async]
        received.append(options)

    with (
        patch("meow.agents.docs_updater.load_config", return_value=config),
        patch("meow.agents.docs_updater.Agent.run_query", side_effect=capture),
    ):
        asyncio.run(update_documentation(prepared))
    assert not {"Edit", "Write"} & set(received[0].allowed_tools)
    callback = received[0].can_use_tool
    source = asyncio.run(callback("Write", {"file_path": "src/app.py"}, None))
    documentation = asyncio.run(callback("Write", {"file_path": "docs/CLI.md"}, None))
    assert type(source).__name__ == "PermissionResultDeny"
    assert type(documentation).__name__ == "PermissionResultAllow"

import asyncio

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from meow.agents.base import Agent, ProjectContext
from meow.agents.mcp_fetcher import IssueFetcherAgent
from meow.agents.orchestration import DecomposerAgent, OrchestratorAgents
from meow.project import prompts


def decomposer(tmp_path) -> DecomposerAgent:
    config = {"models": {"decomposer": None, "reviewer": None}}
    return DecomposerAgent(ProjectContext(tmp_path, config))


def capture_query(monkeypatch, *, write: str | None, output_file) -> dict:
    captured: dict = {}

    async def fake_run_query(prompt, options, role):  # ruff: ignore[unused-async]
        captured.update(prompt=prompt, options=options, role=role)
        if write is not None:
            output_file.write_text(write, encoding="utf-8")
        return ""

    monkeypatch.setattr(Agent, "run_query", staticmethod(fake_run_query))
    return captured


def test_decomposer_is_read_only_apart_from_its_output_file(tmp_path, monkeypatch):
    output_file = tmp_path / "orch" / "tasks.json"
    output_file.parent.mkdir()
    captured = capture_query(monkeypatch, write="{}", output_file=output_file)

    result = asyncio.run(decomposer(tmp_path).run("Build it", output_file))

    options = captured["options"]
    assert result == output_file
    assert captured["role"] == "Decomposer"
    assert captured["prompt"] == "Objective:\nBuild it"
    assert str(output_file) in options.system_prompt
    assert "32" in options.system_prompt
    assert {"Read", "Grep", "Glob"} <= set(options.allowed_tools)
    assert "Bash" not in options.allowed_tools
    assert "Write" not in options.allowed_tools  # routed through the guard

    async def decide(path):
        return await options.can_use_tool("Write", {"file_path": str(path)}, None)

    assert isinstance(asyncio.run(decide(output_file)), PermissionResultAllow)
    assert isinstance(asyncio.run(decide(tmp_path / "other.py")), PermissionResultDeny)


def test_correction_and_drafts_reach_the_prompts(tmp_path, monkeypatch):
    output_file = tmp_path / "tasks.json"
    captured = capture_query(monkeypatch, write="{}", output_file=output_file)

    asyncio.run(
        decomposer(tmp_path).run(
            "Fix bugs",
            output_file,
            task_drafts="- PROJ-1: Fix login",
            correction="missing tasks",
        )
    )

    assert "Your previous output was rejected: missing tasks" in captured["prompt"]
    assert "- PROJ-1: Fix login" in captured["options"].system_prompt


def test_missing_output_is_an_error_and_stale_output_is_removed(tmp_path, monkeypatch):
    output_file = tmp_path / "tasks.json"
    output_file.write_text("stale", encoding="utf-8")
    capture_query(monkeypatch, write=None, output_file=output_file)

    with pytest.raises(RuntimeError, match="Decomposer did not write"):
        asyncio.run(decomposer(tmp_path).run("Build it", output_file))

    assert not output_file.exists()


def test_orchestrator_agents_default_to_the_real_roles():
    agents = OrchestratorAgents()

    assert agents.decomposer is DecomposerAgent
    assert agents.issue_fetcher is IssueFetcherAgent


def test_tasks_format_names_every_task_key():
    text = prompts.tasks_format()

    for key in ("id", "request", "depends_on", "expected_paths", "source_ref"):
        assert f'"{key}"' in text


def test_only_jira_prompts_fix_the_task_list(tmp_path):
    output_file = tmp_path / "tasks.json"

    plain = prompts.decomposer_prompt(output_file, max_tasks=7)
    jira = prompts.decomposer_prompt(
        output_file, max_tasks=7, task_drafts="Task PROJ-1 (PROJ-1):\nFix login"
    )

    assert "at most 7" in plain
    assert "already defined" not in plain
    assert "already defined" in jira
    assert "Task PROJ-1 (PROJ-1):\nFix login" in jira
    assert str(output_file) in plain and str(output_file) in jira

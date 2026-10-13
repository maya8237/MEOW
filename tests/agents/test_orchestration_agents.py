import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from meow.agents.base import Agent, ProjectContext
from meow.agents.orchestration import (
    IntegratorAgent,
    OrchestratorAgents,
    TriageAgent,
)


def run_resolve(worktree):
    """Run `IntegratorAgent.resolve` with the SDK stubbed; return what it sent."""
    context = ProjectContext(
        worktree, {"models": {"reviewer": None}, "lint": []}, use_worktree=True
    )
    calls = []

    async def capture(prompt, options, role):  # ruff: ignore[unused-async]
        calls.append(SimpleNamespace(prompt=prompt, options=options, role=role))

    with patch.object(Agent, "run_query", side_effect=capture):
        asyncio.run(
            IntegratorAgent(context).resolve(
                ["shared.py", "docs/notes.md"], "add the UI", ["add the API"]
            )
        )
    return calls[0]


def test_integrator_write_outside_conflicted_paths_is_denied(tmp_path):
    callback = run_resolve(tmp_path).options.can_use_tool

    denied_write = asyncio.run(
        callback("Write", {"file_path": str(tmp_path / "other.py")}, None)
    )
    denied_edit = asyncio.run(callback("Edit", {"file_path": "other.py"}, None))
    allowed_write = asyncio.run(
        callback("Write", {"file_path": str(tmp_path / "shared.py")}, None)
    )
    allowed_edit = asyncio.run(callback("Edit", {"file_path": "docs/notes.md"}, None))

    assert type(denied_write).__name__ == "PermissionResultDeny"
    assert type(denied_edit).__name__ == "PermissionResultDeny"
    assert "shared.py, docs/notes.md" in denied_write.message
    assert type(allowed_write).__name__ == "PermissionResultAllow"
    assert type(allowed_edit).__name__ == "PermissionResultAllow"


def test_integrator_has_no_shell_and_guards_writes_from_auto_approval(tmp_path):
    sent = run_resolve(tmp_path)

    assert sent.options.tools == ["Read", "Grep", "Glob", "Edit", "Write"]
    assert "Bash" not in sent.options.allowed_tools
    assert not {"Write", "Edit"} & set(sent.options.allowed_tools)
    assert sent.role == "Integrator"


def test_integrator_prompts_name_the_files_and_the_requests(tmp_path):
    sent = run_resolve(tmp_path)

    assert "- shared.py\n- docs/notes.md" in sent.options.system_prompt
    assert str(tmp_path) in sent.options.system_prompt
    assert "Task being merged:\nadd the UI" in sent.prompt
    assert "Tasks already merged:\n- add the API" in sent.prompt


def test_orchestrator_agents_default_to_the_integrator_class():
    assert OrchestratorAgents().integrator is IntegratorAgent


def run_triage(worktree, stale=False):
    """Run `TriageAgent.run` with the SDK stubbed; return what it sent."""
    context = ProjectContext(
        worktree, {"models": {"reviewer": None}, "lint": []}, use_worktree=False
    )
    output = worktree / ".meow" / "orchestrators" / "0123456789ab" / "decision.json"
    if stale:
        output.parent.mkdir(parents=True)
        output.write_text("stale", encoding="utf-8")
    calls = []

    async def capture(prompt, options, role):  # ruff: ignore[unused-async]
        calls.append(SimpleNamespace(prompt=prompt, options=options, role=role))

    with patch.object(Agent, "run_query", side_effect=capture):
        returned = asyncio.run(
            TriageAgent(context).run(
                "add the login page",
                "child-failed",
                ["/runs/a.json", "/runs/b.log"],
                output,
                cycles=1,
                max_cycles=3,
            )
        )
    return returned, output, calls[0]


def test_triage_agent_may_write_only_its_decision_file(tmp_path):
    _returned, output, sent = run_triage(tmp_path)
    callback = sent.options.can_use_tool

    allowed = asyncio.run(callback("Write", {"file_path": str(output)}, None))
    denied = asyncio.run(
        callback("Write", {"file_path": str(tmp_path / "src" / "app.py")}, None)
    )

    assert type(allowed).__name__ == "PermissionResultAllow"
    assert type(denied).__name__ == "PermissionResultDeny"
    assert sent.options.tools == ["Read", "Grep", "Glob", "Write"]
    assert "Write" not in sent.options.allowed_tools
    assert sent.role == "Triage"


def test_triage_agent_sends_the_contract_and_the_failure(tmp_path):
    returned, output, sent = run_triage(tmp_path)

    assert returned == output
    assert str(output) in sent.options.system_prompt
    assert '"rationale" (required)' in sent.options.system_prompt
    assert "Task request:\nadd the login page" in sent.prompt
    assert "Failure cause: child-failed" in sent.prompt
    assert "Recovery cycles used: 1 of 3" in sent.prompt
    assert "- /runs/a.json\n- /runs/b.log" in sent.prompt


def test_triage_agent_removes_a_stale_decision_file(tmp_path):
    _returned, output, _sent = run_triage(tmp_path, stale=True)

    assert not output.exists()


def test_orchestrator_agents_default_to_the_triage_class():
    assert OrchestratorAgents().triage is TriageAgent

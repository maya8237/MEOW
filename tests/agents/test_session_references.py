import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from meow.agents.base import Agent, ProjectContext, log_stream_message
from meow.execution.run_state import RunStore


def _context(tmp_path, store=None, run_id=None):
    config = {
        "models": {"planner": "test-model"},
        "lint": [],
        "agent_skills": {
            "default": ["shared-skill", "duplicate-skill"],
            "planner": ["planner-skill", "duplicate-skill"],
        },
    }
    if store is not None:
        config["_run_journal"] = (store, run_id)
    return ProjectContext(tmp_path, config)


def test_agent_options_merge_default_and_role_skills(tmp_path):
    options = Agent(_context(tmp_path)).options(
        system_prompt="test",
        allowed_tools=["Read"],
        role="planner",
        skills=["builtin-skill", "shared-skill"],
    )

    assert options.skills == [
        "shared-skill",
        "duplicate-skill",
        "planner-skill",
        "builtin-skill",
    ]


def test_agent_options_resumes_and_records_only_session_ids(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.set_session(record.id, "planner", "saved-session")
    context = _context(tmp_path, store, record.id)
    context.config["_resume_required_roles"] = {"planner"}
    options = Agent(context).options(
        system_prompt="test",
        allowed_tools=["Read"],
        role="planner",
    )

    assert options.resume == "saved-session"
    log_stream_message(
        "planner",
        SystemMessage(subtype="init", data={"session_id": "new-session"}),
        options=options,
    )
    log_stream_message(
        "planner",
        ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="new-session",
            result="done",
        ),
        options=options,
    )

    saved = store.load(record.id)
    assert saved.sessions == {"planner": "new-session"}
    assert "content" not in str(saved)


def test_required_resume_session_cannot_start_fresh(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    context = _context(tmp_path, store, record.id)
    context.config["_resume_required_roles"] = {"planner"}

    with pytest.raises(RuntimeError, match="planner session is missing"):
        Agent(context).options(
            system_prompt="test", allowed_tools=["Read"], role="planner"
        )


def test_saved_sessions_resume_only_once_and_only_when_resuming(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.set_session(record.id, "planner", "saved-session")
    context = _context(tmp_path, store, record.id)

    def build():
        return Agent(context).options(
            system_prompt="test", allowed_tools=["Read"], role="planner"
        )

    # A normal run's later rounds start a fresh session.
    assert build().resume is None
    context.config["_resume_required_roles"] = {"planner"}
    assert build().resume == "saved-session"
    assert build().resume is None

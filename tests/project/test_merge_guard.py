"""MEOW never merges a PR/MR (or pushes to its target) unless the user asked."""

import asyncio
import io
import json
import subprocess

import pytest

from meow.infrastructure.user_hooks import BLOCK_EXIT_CODE
from meow.project import merge_guard

PROTECTED = {"main", "master", "dev"}


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 38 --squash",
        "gh pr merge --auto --merge",
        "cd repo && glab mr merge 12",
        "glab mr accept 12",
        "hub merge https://github.com/o/r/pull/1",
        "gh api -X PUT repos/o/r/pulls/38/merge",
        "curl -X PUT https://gitlab.example/api/v4/projects/1/merge_requests/5/merge",
        "curl -d merge_when_pipeline_succeeds=true https://gitlab.example/api",
        "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) }'",
        "git push origin HEAD:main",
        "git push origin feature:refs/heads/master",
        "git push -f origin +HEAD:dev",
        "git -C /repo push origin main",
        "git push origin dev",
    ],
)
def test_merges_and_target_pushes_are_refused(command):
    assert merge_guard.reason("Bash", {"command": command}, PROTECTED) is not None


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --base main --title x",
        "gh pr view 38",
        "git push -u origin feature/dark-mode",
        "git merge --no-ff task-branch",
        "git fetch origin main",
        "git log origin/main..HEAD",
        "glab mr create --target-branch dev",
        "echo merge conflicts resolved",
    ],
)
def test_ordinary_delivery_commands_pass(command):
    assert merge_guard.reason("Bash", {"command": command}, PROTECTED) is None


@pytest.mark.parametrize(
    ("tool", "refused"),
    [
        ("mcp__github__merge_pull_request", True),
        ("mcp__github__enable_pr_auto_merge", True),
        ("mcp__gitlab__accept_merge_request", True),
        ("mcp__gitlab__merge_merge_request", True),
        ("mcp__github__create_pull_request", False),
        ("mcp__gitlab__create_merge_request", False),
        ("mcp__github__list_pull_requests", False),
        ("Write", False),
    ],
)
def test_merge_tools_are_refused(tool, refused):
    assert (merge_guard.reason(tool, {}, PROTECTED) is not None) is refused


@pytest.mark.parametrize(
    ("prompt", "asked"),
    [
        ("merge it", True),
        ("Looks good, please merge the PR", True),
        ("/meow:run Add dark mode and merge the MR when green", True),
        ("squash and land this branch", True),
        ("fix the merge conflicts in the PR", False),
        ("don't merge the PR yet", False),
        ("never merge it", False),
        ("Add dark mode", False),
    ],
)
def test_asked_to_merge(prompt, asked):
    assert merge_guard.asked_to_merge(prompt) is asked


def transcript(tmp_path, *entries):
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")
    return str(path)


def typed(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_latest_user_message_skips_skill_text_and_tool_results(tmp_path):
    path = transcript(
        tmp_path,
        typed("please merge the PR"),
        typed("Add dark mode"),
        {"type": "user", "isMeta": True, "message": {"content": "merge it now"}},
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "merge it"}]},
        },
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}},
    )
    assert merge_guard.latest_user_message(path) == "Add dark mode"
    assert merge_guard.latest_user_message(None) == ""


def test_config_allow_merge_is_validated(tmp_path):
    from meow.project.config import load_config

    (tmp_path / ".meow").mkdir()
    config = tmp_path / ".meow" / "config.toml"
    assert load_config(tmp_path)["delivery"]["allow_merge"] is False
    config.write_text("[delivery]\nallow_merge = true\n", encoding="utf-8")
    assert merge_guard.merge_allowed(load_config(tmp_path)) is True
    config.write_text('[delivery]\nallow_merge = "yes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="allow_merge"):
        load_config(tmp_path)


# Every SDK agent MEOW starts ------------------------------------------------


class Context:
    def __init__(self, tmp_path, config):
        self.project_dir = tmp_path
        self.config = config
        self._dir = tmp_path

    def model(self, role, agent=None):  # ruff: ignore[no-self-use]
        return "m"

    def active_working_dir(self):
        return self._dir


def guard_output(options, tool, tool_input):
    matchers = options.hooks["PreToolUse"]
    hook = matchers[0].hooks[0]
    payload = {"tool_name": tool, "tool_input": tool_input}
    return asyncio.run(hook(payload, None, None))


@pytest.mark.parametrize("mode", [None, "bypassPermissions", "dontAsk"])
def test_every_agent_refuses_merges_first(tmp_path, mode):
    from meow.agents.base import Agent

    extra = {"permission_mode": mode} if mode else {}
    options = Agent(Context(tmp_path, {"delivery": {"target_branch": "dev"}})).options(
        system_prompt="p",
        allowed_tools=["Bash", "mcp__github__*"],
        role="generator",
        **extra,
    )
    denied = guard_output(options, "Bash", {"command": "gh pr merge 1"})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    pushed = guard_output(options, "Bash", {"command": "git push origin HEAD:dev"})
    assert pushed["hookSpecificOutput"]["permissionDecision"] == "deny"
    tool = guard_output(options, "mcp__github__merge_pull_request", {})
    assert tool["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert guard_output(options, "Bash", {"command": "pytest -q"}) == {}


def test_allow_merge_config_lifts_the_agent_guard(tmp_path):
    from meow.agents.base import Agent

    config = {"delivery": {"target_branch": "dev", "allow_merge": True}}
    options = Agent(Context(tmp_path, config)).options(
        system_prompt="p", allowed_tools=["Bash"], role="generator"
    )
    assert not (options.hooks or {}).get("PreToolUse")


# Native sessions: the skills' PreToolUse hook --------------------------------


def run_hook(tmp_path, monkeypatch, payload):
    from meow.native.native_hooks import merge_guard_hook

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return merge_guard_hook(tmp_path, tmp_path)


@pytest.fixture
def project(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    return tmp_path


def test_native_hook_blocks_an_unrequested_merge(project, monkeypatch, capsys):
    path = transcript(project, typed("Add dark mode"))
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 3"},
        "transcript_path": path,
    }
    with pytest.raises(SystemExit) as exit_info:
        run_hook(project, monkeypatch, payload)
    assert exit_info.value.code == BLOCK_EXIT_CODE
    assert "never merges" in capsys.readouterr().err


def test_native_hook_allows_a_merge_the_user_asked_for(project, monkeypatch):
    path = transcript(project, typed("great, merge the PR"))
    payload = {
        "tool_name": "mcp__github__merge_pull_request",
        "tool_input": {},
        "transcript_path": path,
    }
    assert run_hook(project, monkeypatch, payload) == {}


def test_native_hook_ignores_other_commands(project, monkeypatch):
    payload = {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}
    assert run_hook(project, monkeypatch, payload) == {}


def test_skills_register_the_native_hook():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("run", "review", "autopilot"):
        frontmatter = (root / "skills" / name / "SKILL.md").read_text().split("---")[1]
        assert "meow native merge-guard" in frontmatter, name
        assert 'matcher: "Bash|mcp__.*"' in frontmatter, name


def test_ui_claude_sessions_carry_the_guard(tmp_path):
    from meow.ui.sessions import merge_guard_settings

    settings = json.loads(merge_guard_settings(tmp_path, "python"))
    (matcher,) = settings["hooks"]["PreToolUse"]
    assert matcher["matcher"] == "Bash|mcp__.*"
    assert "native merge-guard --work-dir" in matcher["hooks"][0]["command"]

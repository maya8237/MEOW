"""User hook commands: exit-code contract and SDK wiring."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from meow.infrastructure.user_hooks import (
    feedback,
    matching,
    merge_hooks,
    run_hook,
    sdk_hooks,
)
from meow.project.custom import CustomHook, CustomSource

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell quoting")


def make_hook(name="h", event="PreToolUse", script="", **fields):
    source = CustomSource("hooks", Path("."), "project", None)
    code = f"import sys; {script}"
    return CustomHook(
        name=name,
        description="d",
        event=event,
        command=f'"{sys.executable}" -c "{code}"',
        file=Path(f"{name}.toml"),
        source=source,
        **fields,
    )


def run(hook, tmp_path, payload=None):
    return asyncio.run(run_hook(hook, payload or {}, tmp_path, "generator"))


def test_exit_two_blocks_with_stderr(tmp_path):
    hook = make_hook(script="sys.stderr.write('no'); sys.exit(2)")

    result = run(hook, tmp_path)

    assert result.blocking and result.stderr == "no"


def test_hook_gets_payload_and_env(tmp_path):
    script = (
        "import json,os; d=json.load(sys.stdin); "
        "ok=d['tool_name']=='Write' and d['hook_event_name']=='PreToolUse' "
        "and os.environ['MEOW_ROLE']=='generator'; sys.exit(0 if ok else 3)"
    )

    result = run(make_hook(script=script), tmp_path, {"tool_name": "Write"})

    assert result.exit_code == 0


def test_other_exit_codes_and_timeouts_do_not_block(tmp_path):
    assert not run(make_hook(script="sys.exit(1)"), tmp_path).blocking
    slow = make_hook(script="import time; time.sleep(30)", timeout=0.3)
    assert run(slow, tmp_path).exit_code is None


def test_matcher_filters_tools():
    hook = make_hook(matcher="Write|Edit")

    assert matching([hook], {"tool_name": "Edit"}) == [hook]
    assert matching([hook], {"tool_name": "EditNotebook"}) == []


def test_feedback_never_allows():
    deny = feedback("PreToolUse", "x")["hookSpecificOutput"]
    assert deny["permissionDecision"] == "deny"
    assert "additionalContext" in feedback("PostToolUse", "x")["hookSpecificOutput"]
    assert feedback("Stop", "x") == {"decision": "block", "reason": "x"}


def test_sdk_hook_callback_denies_on_exit_two(tmp_path):
    hook = make_hook(script="sys.stderr.write('stop'); sys.exit(2)")
    table = sdk_hooks([hook], tmp_path, "generator")

    callback = table["PreToolUse"][0].hooks[0]
    out = asyncio.run(callback({"tool_name": "Bash"}, None, None))

    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "Hook h: stop"


def test_sdk_hooks_skip_other_roles_and_lifecycle(tmp_path):
    limited = make_hook(roles=("reviewer",))
    lifecycle = make_hook(name="l", event="sprint_end")

    assert sdk_hooks([limited, lifecycle], tmp_path, "generator") == {}


def test_merge_keeps_every_matcher():
    assert merge_hooks({"A": [1]}, None, {"A": [2], "B": [3]}) == {
        "A": [1, 2],
        "B": [3],
    }


def test_agent_options_append_user_hooks_after_code_owned_ones(tmp_path):
    from claude_agent_sdk import HookMatcher

    from meow.agents.base import Agent
    from meow.project.custom import Customizations

    class Context:
        project_dir = tmp_path

        def __init__(self):
            self.config = {"customizations": Customizations(hooks=(make_hook(),))}

        def model(self, role, agent=None):  # ruff: ignore[no-self-use]
            return "m"

        def active_working_dir(self):  # ruff: ignore[no-self-use]
            return tmp_path

    builtin = HookMatcher(matcher="Write|Edit", hooks=[])
    options = Agent(Context()).options(
        system_prompt="p",
        allowed_tools=["Read"],
        role="generator",
        hooks={"PreToolUse": [builtin]},
    )

    assert options.hooks["PreToolUse"][0] is builtin
    assert len(options.hooks["PreToolUse"]) == len([builtin, None])


def test_lifecycle_hooks_run_and_never_raise(tmp_path):
    from meow.infrastructure.user_hooks import run_lifecycle

    marker = tmp_path / "seen"
    ran = make_hook(
        event="sprint_end",
        script=(
            f"import json; open({str(marker)!r}, 'w')"
            ".write(json.load(sys.stdin)['phase'])"
        ),
    )
    blocking = make_hook(name="b", event="sprint_end", script="sys.exit(2)")
    other = make_hook(name="o", event="sprint_start", script="sys.exit(2)")

    asyncio.run(
        run_lifecycle([blocking, ran, other], "sprint_end", {"phase": "done"}, tmp_path)
    )

    assert marker.read_text() == "done"

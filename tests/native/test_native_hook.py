"""`meow native hook` and `meow native lint --from-hook`."""

import io
import json
import os
import sys

import pytest

from tests.native.test_native_cli import run_native

BLOCKED = 2
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell quoting")


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = tmp_path / "project"
    (root / ".meow" / "hooks").mkdir(parents=True)
    return root


def add_hook(root, name, body, extra=""):
    command = json.dumps(f'"{sys.executable}" -c "{body}"')
    (root / ".meow" / "hooks" / f"{name}.toml").write_text(
        f'description = "d"\nevent = "PreToolUse"\ncommand = {command}\n{extra}',
        encoding="utf-8",
    )


def feed(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def test_blocking_hook_exits_two_with_reason(project, monkeypatch):
    add_hook(project, "guard", "import sys; sys.stderr.write('nope'); sys.exit(2)")
    feed(monkeypatch, {"tool_name": "Write"})

    code, _, err = run_native("hook", "PreToolUse", "-d", str(project))

    assert code == BLOCKED and "Hook guard: nope" in err


def test_passing_hook_prints_empty_result(project, monkeypatch):
    add_hook(project, "ok", "pass")
    feed(monkeypatch, {"tool_name": "Write"})

    code, out, _ = run_native("hook", "PreToolUse", "-d", str(project))

    assert (code, json.loads(out)) == (0, {})


def test_role_limited_hook_needs_matching_role(project, monkeypatch):
    add_hook(
        project, "guard", "import sys; sys.exit(2)", extra='roles = ["generator"]\n'
    )

    feed(monkeypatch, {})
    assert run_native("hook", "PreToolUse", "-d", str(project))[0] == 0
    feed(monkeypatch, {})
    code = run_native("hook", "PreToolUse", "--role", "generator", "-d", str(project))
    assert code[0] == BLOCKED


def test_lint_from_hook_reports_problems(project, monkeypatch):
    fail = json.dumps(f"{sys.executable} -c 'import sys; print(\"bad\"); sys.exit(1)'")
    (project / ".meow" / "config.toml").write_text(
        f"[[lint]]\ncommand = {fail}\nper_file = true\n", encoding="utf-8"
    )
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    feed(monkeypatch, {"tool_input": {"file_path": "a.py"}})

    code, out, _ = run_native("lint", "--from-hook", "-d", str(project))

    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert code == 0 and "Lint issues in a.py" in context


def test_lint_from_hook_ignores_payload_without_file(project, monkeypatch):
    feed(monkeypatch, {"tool_input": {}})

    code, out, _ = run_native("lint", "--from-hook", "-d", str(project))

    assert (code, json.loads(out)) == (0, {})


def test_name_selects_one_definition(project, monkeypatch):
    add_hook(project, "a", "import sys; sys.exit(2)")
    add_hook(project, "b", "pass")
    feed(monkeypatch, {})

    code = run_native("hook", "PreToolUse", "--name", "b", "-d", str(project))

    assert code[0] == 0


def test_sdk_agents_skip_the_dispatcher(project, monkeypatch):
    add_hook(project, "guard", "import sys; sys.exit(2)")
    monkeypatch.setenv("MEOW_SDK_AGENT", "1")
    feed(monkeypatch, {})

    code, out, _ = run_native("hook", "PreToolUse", "-d", str(project))

    assert (code, json.loads(out)) == (0, {})

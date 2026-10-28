"""Startup and kill paths must not depend on WMI or wait on Windows tools forever."""

import ast
import re
import subprocess
import types
from pathlib import Path

import pytest

from meow.common import process as process_module
from meow.common.safe_platform import WMI_DEPENDENT_COMMANDS
from meow.execution.run_failures import (
    TransientRunError,
    WindowsQueryTimeout,
    classify_failure,
)
from meow.infrastructure import checks, scheduled_tasks

SRC = Path(__file__).resolve().parents[2] / "src" / "meow"
_WORDS = re.compile(
    "|".join(rf"\b{re.escape(name)}\b" for name in WMI_DEPENDENT_COMMANDS),
    re.IGNORECASE,
)
# file -> reason the WMI command names may appear there.
ALLOWED = {
    "common/safe_platform.py": "defines WMI_DEPENDENT_COMMANDS for this audit",
}
_WINDOWS_TOOLS = ("taskkill", "schtasks", "powershell", "cmd")
_RUNNERS = {"run", "check_output", "check_call", "call"}


def _sources():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_audit_no_wmi_commands_in_src():
    offenders = []
    for path in _sources():
        relative = path.relative_to(SRC).as_posix()
        if relative in ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _WORDS.search(node.value)
            ):
                offenders.append(f"{relative}:{node.lineno}: {node.value[:60]!r}")
    assert not offenders, offenders


def _strings(node):
    return [
        n.value.lower()
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_windows_subprocess_calls_have_timeouts():
    offenders = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _RUNNERS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.args
            ):
                continue
            names = {s.split()[0] for s in _strings(node.args[0]) if s.split()}
            if names & set(_WINDOWS_TOOLS) and not any(
                kw.arg == "timeout" for kw in node.keywords
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, offenders


def test_no_platform_queries_for_control_flow():
    queries = {"system", "uname", "platform", "release", "version", "machine"}
    offenders = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "platform"
                and node.func.attr in queries
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, offenders


def _timeout(*_args, **kwargs):
    raise subprocess.TimeoutExpired("x", kwargs.get("timeout", 0))


def test_kill_helpers_degrade_on_taskkill_timeout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _timeout)
    assert process_module.run_windows_tool(["taskkill"]) is None

    killed = []

    proc = types.SimpleNamespace(pid=1, kill=lambda: killed.append(True))
    monkeypatch.setattr(checks.os, "name", "nt")
    checks.kill_process_tree(proc)
    assert killed == [True]


@pytest.mark.parametrize("tool", ["schtasks", "whoami"])
def test_scheduler_timeout_is_transient(monkeypatch, tool):
    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(TransientRunError) as caught:
        scheduled_tasks.scheduler_command([tool])
    assert caught.value.cause == "windows_query_timeout"
    assert isinstance(caught.value, ValueError)
    assert classify_failure(WindowsQueryTimeout("slow")) == (
        "transient",
        "windows_query_timeout",
    )


def test_non_windows_scheduler_timeout_stays_a_value_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(ValueError, match="timed out") as caught:
        scheduled_tasks.scheduler_command(["systemctl", "status"])
    assert not isinstance(caught.value, TransientRunError)

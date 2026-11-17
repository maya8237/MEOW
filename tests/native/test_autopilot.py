"""`meow autopilot`: flag copying, the generic loop engine, and native runs.

No test launches Claude or a real meow run: commands are tiny Python scripts.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from meow.common.process import process_identity
from meow.execution.run_state import RunStore
from meow.infrastructure import autopilot_loop as loops
from meow.infrastructure.cancellation import cancel_requested, request_cancel
from meow.infrastructure.claude_flags import filter_flags, merge_flags, parse_help
from meow.native import autopilot
from meow.native.native_state import checkpoint

KINDS = {
    "--model": "one",
    "--add-dir": "many",
    "--resume": "maybe",
    "-r": "maybe",
    "--session-id": "one",
    "--permission-mode": "one",
}
PY = sys.executable


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(loops, "POLL_SECONDS", 0.01)
    for name in (loops.LOOP_ENV, autopilot.SESSION_ENV, autopilot.CLAUDE_PID_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def own_loop(repo, **fields) -> loops.LoopState:
    """A loop whose supervisor is this test process (so it counts as alive)."""
    state = loops.LoopState(
        id=loops.new_id(),
        repo=str(repo),
        pid=os.getpid(),
        identity=process_identity(os.getpid()),
        **fields,
    )
    return loops.save(state)


def native_run(repo, phase="generator_started", **extra):
    return checkpoint(repo, repo, phase, request="do work", intent="run", **extra)


# Flags -------------------------------------------------------------------


def test_filter_flags_keeps_settings_and_drops_prompt_and_session():
    args = [
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--model",
        "opus",
        "--add-dir",
        "../a",
        "../b",
        "--resume",
        "abc",
        "-p",
        "--session-id",
        "s1",
        "fix the bug",
    ]
    assert filter_flags(args, KINDS) == [
        "--dangerously-skip-permissions",
        "--model",
        "opus",
        "--add-dir",
        "../a",
        "../b",
    ]


def test_parse_help_reads_value_kinds():
    text = (
        "Options:\n"
        "  --add-dir <directories...>   Dirs\n"
        "  -r, --resume [value]         Resume\n"
        "  --model <model>              Model\n"
        "  --verbose                    Verbose\n"
    )
    assert parse_help(text) == {
        "--add-dir": "many",
        "-r": "maybe",
        "--resume": "maybe",
        "--model": "one",
        "--verbose": "none",
    }


def test_merge_flags_adds_permission_mode_only_when_missing():
    assert merge_flags([], ["--model", "x"], "acceptEdits") == [
        "--model",
        "x",
        "--permission-mode",
        "acceptEdits",
    ]
    skip = ["--dangerously-skip-permissions"]
    assert merge_flags([], skip, "bypassPermissions") == skip
    assert merge_flags(["--verbose"], [], "plan") == ["--verbose"]


def test_expand_template_fills_and_drops_fields():
    argv = loops.expand(
        ["claude", "-p", "{resume}", "{flags}", "{prompt}", "run={run_id}"],
        {"resume": [], "flags": ["--model", "x"], "prompt": "", "run_id": "r1"},
    )
    assert argv == ["claude", "-p", "--model", "x", "run=r1"]


# Questions versus running tools ---------------------------------------------


def write_log(path: Path, *entries) -> str:
    path.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")
    return str(path)


def call(tool_id, name):
    content = [{"type": "tool_use", "id": tool_id, "name": name}]
    return {"type": "assistant", "message": {"content": content}}


def result(tool_id):
    content = [{"type": "tool_result", "tool_use_id": tool_id}]
    return {"type": "user", "message": {"content": content}}


def test_session_state_tells_questions_from_running_tools(tmp_path):
    asking = write_log(tmp_path / "a.jsonl", call("1", "AskUserQuestion"))
    busy = write_log(tmp_path / "b.jsonl", call("1", "Bash"))
    idle = write_log(tmp_path / "c.jsonl", call("1", "Bash"), result("1"))
    assert loops.session_state(asking, 0, 0) == "asking"
    assert loops.session_state(busy, 0, 0) == "busy"
    assert loops.session_state(idle, 0, 0) == "idle"
    # A permission prompt (Notification) waits until the session moves again.
    assert loops.session_state(busy, asking_since=10, activity=5) == "asking"
    assert loops.session_state(idle, asking_since=10, activity=20) == "idle"


def test_running_tool_is_never_killed_but_stalls_and_questions_are(repo, tmp_path):
    state = own_loop(repo, stall=0, answer_wait=0)
    supervisor = loops.Supervisor(state, loops.Backend(state), now=lambda: 1e12)
    busy = write_log(tmp_path / "b.jsonl", call("1", "Bash"))
    question = write_log(tmp_path / "q.jsonl", call("1", "AskUserQuestion"))
    idle = write_log(tmp_path / "i.jsonl", call("1", "Bash"), result("1"))
    assert supervisor._should_kill(busy) is False
    assert supervisor._should_kill(question) is True
    assert supervisor._should_kill(idle) is True
    patient = loops.Supervisor(
        own_loop(repo, stall=1e13, answer_wait=1e13),
        loops.Backend(state),
        now=lambda: 1e12,
    )
    assert patient._should_kill(question) is False


# The generic loop -----------------------------------------------------------


def script(body: str) -> list[str]:
    return [PY, "-c", body]


def test_loop_repeats_until_the_until_check_passes(repo):
    counter = repo / "count"
    body = (
        "from pathlib import Path\n"
        f"p = Path({str(counter)!r})\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "raise SystemExit(1)\n"  # failing exits never end the loop by themselves
    )
    until = f'{PY} -c "import sys; sys.exit(int(open({str(counter)!r}).read()) < 3)"'
    state = loops.save(
        loops.LoopState(
            id=loops.new_id(), repo=str(repo), command=script(body), until=until
        )
    )
    state.backoff = [0]
    final = loops.Supervisor(state, loops.Backend(state), sleep=lambda _s: None).run()
    assert (final.stopped, final.launches, counter.read_text()) == ("done", 3, "3")


def test_loop_without_until_ends_on_a_clean_exit_and_honours_a_cap(repo):
    ok = loops.LoopState(id=loops.new_id(), repo=str(repo), command=script("pass"))
    ok.backoff = [0]
    assert loops.Supervisor(loops.save(ok), loops.Backend(ok)).run().stopped == "done"
    failing = loops.LoopState(
        id=loops.new_id(), repo=str(repo), command=script("raise SystemExit(2)"), cap=2
    )
    failing.backoff = [0]
    final = loops.Supervisor(
        loops.save(failing), loops.Backend(failing), sleep=lambda _s: None
    ).run()
    assert (final.stopped, final.launches, final.last_exit) == ("cap", 2, 2)


def test_stop_marker_ends_the_loop_and_kills_the_session(repo):
    state = loops.save(
        loops.LoopState(
            id=loops.new_id(),
            repo=str(repo),
            command=script("import time; time.sleep(60)"),
            backoff=[0],
        )
    )
    calls = []
    running_by_now = 3

    def sleep(_seconds):
        calls.append(1)
        if len(calls) == running_by_now:  # the session runs: ask to stop
            loops.marker(repo, state.id, "stop").write_text("stop", encoding="utf-8")

    final = loops.Supervisor(state, loops.Backend(state), sleep=sleep).run()
    assert final.stopped == "stopped"
    assert final.last_exit not in {None, 0}
    assert not loops.marker(repo, state.id, "stop").exists()


def test_missing_command_is_retried_not_fatal(repo):
    state = loops.save(
        loops.LoopState(
            id=loops.new_id(),
            repo=str(repo),
            command=["definitely-not-a-real-command-xyz"],
            backoff=[0],
            cap=2,
        )
    )
    final = loops.Supervisor(state, loops.Backend(state), sleep=lambda _s: None).run()
    assert (final.stopped, final.launches) == ("cap", 2)


# Native runs ----------------------------------------------------------------


def test_checkpoint_autopilot_starts_a_watcher_for_this_session(repo, monkeypatch):
    started = []
    monkeypatch.setattr(loops, "detach", started.append)
    monkeypatch.setenv(autopilot.SESSION_ENV, "session-1")
    monkeypatch.setenv(autopilot.CLAUDE_PID_ENV, str(os.getpid()))
    created = checkpoint(repo, repo, "preparing", intent="run", autopilot=True)
    record = RunStore(repo).load(created["run_id"])
    state = loops.load(repo, created["autopilot"]["loop"])
    assert started == [state]
    assert (state.kind, state.run_id, state.watch_session) == (
        "run",
        record.id,
        "session-1",
    )
    assert record.results["claude_session_id"] == "session-1"
    assert record.results["autopilot"] == {"loop": state.id}


def test_checkpoint_inside_a_launched_session_joins_its_loop(repo, monkeypatch):
    monkeypatch.setattr(loops, "detach", lambda _state: pytest.fail("no new loop"))
    monkeypatch.setenv(loops.LOOP_ENV, "abc123")
    created = checkpoint(repo, repo, "preparing", intent="run", autopilot=True)
    record = RunStore(repo).load(created["run_id"])
    assert record.results["autopilot"] == {"loop": "abc123"}


def link(repo, run_id, **fields) -> loops.LoopState:
    state = own_loop(repo, kind="run", run_id=run_id, **fields)
    RunStore(repo).update_results(run_id, autopilot={"loop": state.id})
    return state


def test_watched_session_may_drive_until_takeover(repo, monkeypatch):
    run_id = native_run(repo)["run_id"]
    state = link(repo, run_id, watch_session="s1", state="watching")
    record = RunStore(repo).load(run_id)
    assert autopilot.may_drive(record) is False
    monkeypatch.setenv(autopilot.SESSION_ENV, "s1")
    assert autopilot.may_drive(record) is True
    state.state = "running"
    loops.save(state)
    assert autopilot.may_drive(record) is False
    monkeypatch.setenv(loops.LOOP_ENV, state.id)
    assert autopilot.may_drive(record) is True


def test_success_is_the_only_end_and_failures_are_reopened(repo):
    run_id = native_run(repo)["run_id"]
    state = link(repo, run_id, session="s1", flags=["--model", "x"])
    store = RunStore(repo)
    store.transition(run_id, "failed", last_failure="Required evidence failed")
    backend = autopilot.RunBackend(state)
    assert backend.done(0) is False
    argv, _env, cwd = backend.prepare()
    assert store.load(run_id).phase == "resuming"
    assert argv[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert argv[5:10] == ["--resume", "s1", "--fork-session", "--model", "x"]
    assert run_id in argv[-1] and "failed" in argv[-1]
    assert cwd == str(repo)
    store.transition(run_id, "complete")
    assert backend.done(None) is True


def test_cancel_restarts_an_autopilot_run_instead_of_ending_it(repo):
    run_id = native_run(repo)["run_id"]
    store = RunStore(repo)
    state = link(repo, run_id)
    store.transition(run_id, "exhausted")  # terminal, yet autopilot continues
    request_cancel(store, run_id)
    backend = autopilot.RunBackend(state)
    assert backend.restart_requested() is True
    assert not cancel_requested(store, run_id)
    assert backend.restart_requested() is False


def test_unlimited_autopilot_keeps_parking_without_giving_up(repo):
    run_id = native_run(repo)["run_id"]
    link(repo, run_id)
    store = RunStore(repo)
    for _ in range(8):
        record = store.park(run_id, "API overloaded", "transient", "api")
        assert record.phase == "parked"


def test_tester_projects_continue_through_the_cli(repo):
    (repo / ".meow" / "config.toml").write_text(
        '[[tester.tests]]\ncommand = "npm test"\n', encoding="utf-8"
    )
    run_id = native_run(repo)["run_id"]
    argv, _env, _cwd = autopilot.RunBackend(link(repo, run_id)).prepare()
    assert argv[-6:] == [
        "resume",
        run_id,
        "--continue",
        "--repair",
        "--work-dir",
        str(repo),
    ]


def test_stop_hook_blocks_a_watched_session_and_stopfailure_wakes(repo):
    run_id = native_run(repo)["run_id"]
    state = link(repo, run_id, watch_session="s1", state="watching")
    blocked = autopilot.hook(repo, "Stop", {"session_id": "s1"})
    assert blocked["decision"] == "block" and run_id in blocked["reason"]
    assert autopilot.hook(repo, "Stop", {"session_id": "other"}) == {}
    autopilot.hook(
        repo,
        "Notification",
        {"session_id": "s1", "notification_type": "permission_prompt"},
    )
    assert loops.marker(repo, state.id, "asking").exists()
    assert autopilot.hook(repo, "StopFailure", {"session_id": "s1"}) == {
        "woke": state.id
    }
    RunStore(repo).transition(run_id, "complete")
    assert autopilot.hook(repo, "Stop", {"session_id": "s1"}) == {}


def test_stopfailure_without_a_live_watcher_starts_one(repo, monkeypatch):
    started = []
    monkeypatch.setattr(loops, "detach", started.append)
    run_id = native_run(repo)["run_id"]
    dead = loops.save(
        loops.LoopState(
            id=loops.new_id(),
            repo=str(repo),
            kind="run",
            run_id=run_id,
            watch_session="s1",
            cap=4,
        )
    )
    RunStore(repo).update_results(run_id, autopilot={"loop": dead.id})
    payload = {"session_id": "s1", "permission_mode": "acceptEdits"}
    assert "started" in autopilot.hook(repo, "StopFailure", payload)
    fresh = started[0]
    assert (fresh.run_id, fresh.cap, fresh.session) == (run_id, 4, "s1")
    assert fresh.flags[-2:] == ["--permission-mode", "acceptEdits"]


def test_stop_command_ends_a_loop_that_is_not_running(repo):
    state = loops.save(loops.LoopState(id=loops.new_id(), repo=str(repo)))
    assert autopilot.stop(repo, state.id).stopped == "stopped"


def test_cli_loop_and_status(repo, monkeypatch, capsys):
    from meow.cli.autopilot_cli import autopilot_command

    monkeypatch.setattr(loops, "detach", lambda _state: None)
    code = autopilot_command([
        "loop",
        "Fix lint",
        "--until",
        "ruff check .",
        "--cap",
        "3",
        "-d",
        str(repo),
        "--",
        "--model",
        "x",
        "-p",
    ])
    assert code == 0
    (state,) = loops.all_loops(repo)
    assert (state.prompt, state.until, state.cap, state.flags) == (
        "Fix lint",
        "ruff check .",
        3,
        ["--model", "x"],
    )
    capsys.readouterr()
    assert autopilot_command(["status", state.id, "-d", str(repo)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == state.id

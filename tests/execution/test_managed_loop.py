"""End to end: a managing agent drives runs through every injected fault.

The managing Claude is played by `manage`: it starts `meow run --background`,
then repeats `meow status --json` and runs `next_command` exactly when the
status says the run is settled and retryable, as `skills/run/SKILL.md` tells it
to. Only the agent (a scripted generator behind the real `SessionAgent`) and the
OS-level faults are fake; the CLI, journal, supervisor, park/classify, reconcile
and delivery state run as real code, mostly in real processes. The injections
live in `managed_faults.py`; each test's plan names which launch gets which.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli.cli import cli_main
from meow.common.process import process_identity
from meow.execution import sprint_runner
from meow.execution.queue_state import QueueStore, QueueWorkerLock
from meow.execution.run_state import RunStore
from tests.execution import managed_faults
from tests.native.test_native_cli import run_native
from tests.native.test_native_finalize import _review

FAULTS_FILE = Path(managed_faults.__file__)
SITECUSTOMIZE = """\
import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "meow_e2e_faults", os.environ["MEOW_E2E_FAULTS"]
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.install()
"""
# A real 1 s supervisor backoff keeps the "parked but supervised" window open
# long enough for the managing loop to hit it; the API wait ceiling is 1 s so a
# 2 s model-per-key reset parks instead of sleeping.
# The eleven launches take nine manager resumes (two are the supervisor's own);
# `next_command` spends `managed_resumes` now, so the default six is too few.
RUN_CONFIG = "[run]\nretry_backoff = [1]\napi_max_wait = 1\nmanaged_resumes = 10\n"
FAST_CONFIG = "[run]\nretry_backoff = [0]\n"
DONE = frozenset({"complete", "delivered"})
POLL_SECONDS = 0.3
MAX_ACTIONS = 15
LOOP_SECONDS = 400
COMMAND_SECONDS = 120
EXIT_BLOCKED, EXIT_RETRY, EXIT_KILLED = 1, 75, -9
WEDGED_LAUNCH = 10
OVERLOADS = 3
AUTO_RETRIES = 3  # the [run] default
MANAGED_RESUMES = 6  # the [run] default
RETRY_AFTER = 2.0
SEQUENCE_CLIENTS = 3  # the session and its two rebuilds
OVERLOAD_LADDER = [15.0, 30.0, 60.0, 120.0, 240.0, 300.0]
RENAME_RETRIES = 11  # 9 on the failed transition, 2 more on the park


def step(phase, round_, fault, **extra):
    return {"phase": phase, "round": round_, "fault": fault, **extra}


# The nine injections of the plan, in order (R10 and R11 included).
MAIN_PLAN = [
    step("planning", 0, "winerror_1450"),
    step("generator_started", 1, "exit_137", kill_supervisor=True),
    step("reviewer_started", 1, "sequence_400"),
    step("checking", 1, "rename_denied"),
    *[step("pipeline_waiting", 1, "overload_529")] * OVERLOADS,
    step("pipeline_fixing", 1, "setup_timeout"),
    step("generator_started", 2, "per_key_429"),
    step("reviewer_started", 2, "wedged_wmi"),
    step("checking", 2, "complete"),
]
FAILING_LAUNCHES = len(MAIN_PLAN) - 1
SUPERVISED_LAUNCHES = 2  # the supervisor drives these itself, then is killed


@dataclass
class World:
    """A git repository and the directory the fault plan and events live in."""

    repo: Path
    directory: Path

    def events(self, name=None):
        path = self.directory / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        found = [json.loads(line) for line in lines]
        return [item for item in found if name in {None, item["event"]}]

    def launches(self):
        path = self.directory / "launches"
        return int(path.read_text(encoding="utf-8")) if path.exists() else 0


def git_repo(path, config=""):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(path)], check=True)
    for key, value in (("user.name", "T"), ("user.email", "t@example.com")):
        subprocess.run(["git", "-C", str(path), "config", key, value], check=True)
    (path / ".meow").mkdir(exist_ok=True)
    (path / ".meow" / "config.toml").write_text(config, encoding="utf-8")
    (path / ".gitignore").write_text(".meow/\n.worktrees/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    return path


def build_world(tmp_path, monkeypatch, steps, config):
    repo = git_repo(tmp_path / "repo", config)
    directory = tmp_path / "e2e"
    site = directory / "site"
    site.mkdir(parents=True)
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    (directory / "plan.json").write_text(json.dumps(steps), encoding="utf-8")
    old = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(site), old])))
    monkeypatch.setenv("MEOW_E2E_FAULTS", str(FAULTS_FILE))
    monkeypatch.setenv("MEOW_E2E_DIR", str(directory))
    return World(repo, directory)


@pytest.fixture
def make_world(tmp_path, monkeypatch):
    """`make_world(steps, config)`: a repo whose driver launches follow `steps`."""
    (tmp_path / "repo").mkdir()
    return lambda steps, config=RUN_CONFIG: build_world(
        tmp_path, monkeypatch, steps, config
    )


@pytest.fixture
def in_process(monkeypatch):
    """Run the scripted driver in this process (faults that do not kill it)."""
    monkeypatch.setattr(sprint_runner, "_drive_sprint", managed_faults.drive_sprint)


def meow(world, *args, env=None):
    """Run `python -m meow ARGS` in the repository; return the finished process."""
    return subprocess.run(
        [sys.executable, "-u", "-m", "meow", *args],
        cwd=world.repo,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=COMMAND_SECONDS,
        check=False,
    )


def run_cli(capsys, *argv):
    """`meow ARGV` in this process: (exit code, stdout, stderr)."""
    capsys.readouterr()
    try:
        cli_main(list(argv))
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_BLOCKED
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def contract_line(stderr, prefix):
    """The JSON after the last `PREFIX ` line of a command's stderr, or None."""
    lines = [line for line in stderr.splitlines() if line.startswith(prefix + " ")]
    return json.loads(lines[-1][len(prefix) + 1 :]) if lines else None


def json_line(stdout):
    return json.loads(stdout.strip().splitlines()[-1])


def status_of(world, run_id):
    work_dir = str(world.repo)
    done = meow(world, "status", run_id, "--json", "--work-dir", work_dir)
    assert done.returncode == 0, done.stderr
    return json_line(done.stdout)


def launch_background(world, request="Add a thing"):
    argv = ["run", request, "-n", "--background", "--work-dir", str(world.repo)]
    done = meow(world, *argv)
    assert done.returncode == 0, done.stderr
    return done.stdout.split("Background run: ")[1].split()[0]


@dataclass
class Action:
    command: str
    code: int
    retry: dict | None
    blocked: dict | None
    stderr: str = ""


@dataclass
class Trace:
    """What the managing loop saw and did."""

    statuses: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    waits: list = field(default_factory=list)


def next_action(world, payload, env_for_launch):
    """Run `next_command` exactly, as the skill says; return what happened."""
    argv = shlex.split(payload["next_command"])
    assert argv[:2] == ["meow", "resume"], argv
    done = meow(world, *argv[1:], env=env_for_launch(world.launches() + 1))
    return Action(
        payload["next_command"],
        done.returncode,
        contract_line(done.stderr, "MEOW_RETRY"),
        contract_line(done.stderr, "MEOW_BLOCKED"),
        done.stderr,
    )


def manage(world, run_id, env_for_launch=lambda _launch: {}):
    """The managing Claude: act only on a settled, retryable status."""
    trace = Trace()
    deadline = time.monotonic() + LOOP_SECONDS
    while time.monotonic() < deadline and len(trace.actions) < MAX_ACTIONS:
        payload = status_of(world, run_id)
        trace.statuses.append(payload)
        if payload["phase"] in DONE or (
            payload["settled"] and not payload["retryable"]
        ):
            return trace
        if not payload["settled"]:
            time.sleep(POLL_SECONDS)
            continue
        trace.waits.append(payload["retry_after"])  # honoured, not slept
        trace.actions.append(next_action(world, payload, env_for_launch))
    raise AssertionError(f"managing loop did not settle: {trace}")


def wedged_wmi_on_launch(launch):
    if launch != WEDGED_LAUNCH:
        return {}
    return {"MEOW_E2E_WEDGE_WMI": "1", "MEOW_TEST_FORCE_WIN32": "1"}


def run_files(world, run_id):
    """Everything meow persisted for the run: record, worker log, error log."""
    runs = world.repo / ".meow" / "runs"
    return {
        path.name.removeprefix(run_id): path.read_text(
            encoding="utf-8", errors="replace"
        )
        for path in sorted(runs.glob(f"{run_id}*"))
    }


def parked_payloads(trace, cause):
    return [p for p in trace.statuses if p["phase"] == "parked" and p["cause"] == cause]


def assert_no_human_needed(trace):
    """Only retries, within budget; nothing blocked or asked for a person."""
    codes = [action.code for action in trace.actions]
    assert codes == [EXIT_RETRY] * (len(codes) - 1) + [0]
    assert all(a.retry and not a.blocked for a in trace.actions[:-1])
    assert all(p["budget_left"] > 0 for p in trace.statuses)
    unfinished = [p for p in trace.statuses if p["phase"] not in DONE]
    assert not [p for p in unfinished if p["settled"] and not p["retryable"]]
    assert not [p for p in trace.statuses if "not_resumed_reason" in p]


def assert_faults_visible(world, run_id, trace):
    """Each injected failure shows in a transition, a status or a log."""
    files = run_files(world, run_id)
    record = RunStore(world.repo).load(run_id)
    phases = [item["phase"] for item in record.transitions]
    assert phases.count("parked") == FAILING_LAUNCHES
    assert phases.count("resuming") == FAILING_LAUNCHES
    assert "failed" not in phases
    assert "parked (resource)" in files[".log"]  # 1. the supervisor's own resume
    died = parked_payloads(trace, "driver_died")[0]  # 2. os._exit(137)
    assert "os._exit(137) during generator_started" in died["last_failure"]
    assert "os._exit(137) during generator_started" in files[".error.log"]
    assert parked_payloads(trace, "invalid_sequence")  # 3. LiteLLM sequence 400
    assert files[".error.log"].count("atomic_replace_retry") == RENAME_RETRIES  # 4.
    overloads = parked_payloads(trace, "server_error")  # 5. 529, three launches
    assert [p["attempts"] for p in overloads] == list(range(1, OVERLOADS + 1))
    assert files[".error.log"].count("api_backoff") == OVERLOADS * len(OVERLOAD_LADDER)
    assert parked_payloads(trace, "setup_timeout")  # 6. uv sync timed out twice
    assert "setup_command_timeout_retry" in files[".error.log"]


def assert_api_policy_and_platform(world, trace):
    """The API ladders ran as designed and the wedged-WMI driver started."""
    sleeps = [event["seconds"] for event in world.events("api_sleep")]
    assert sleeps == OVERLOAD_LADDER * OVERLOADS
    counts = [event["count"] for event in world.events("generator_clients")]
    assert counts[0] == SEQUENCE_CLIENTS  # the sequence 400: two rebuilds
    per_key = parked_payloads(trace, "model_key_rate_limit")  # 7. R11
    assert [p["retry_after"] for p in per_key] == [RETRY_AFTER]
    assert RETRY_AFTER in trace.waits
    started = world.events("wmi_start")  # 8. R10
    assert [e["system"] for e in started] == ["Windows"]


def assert_key_never_persisted(world, run_id):
    texts = run_files(world, run_id).values()
    assert all(managed_faults.KEY not in text for text in texts)


def test_managed_loop_reaches_complete(make_world):
    world = make_world(MAIN_PLAN)
    run_id = launch_background(world)

    trace = manage(world, run_id, wedged_wmi_on_launch)

    final = status_of(world, run_id)
    assert final["phase"] in DONE
    assert final["next_command"] is None
    assert world.launches() == len(MAIN_PLAN)
    assert [e["fault"] for e in world.events("launch")] == [
        s["fault"] for s in MAIN_PLAN
    ]
    assert len(trace.actions) == FAILING_LAUNCHES - SUPERVISED_LAUNCHES + 1
    assert_no_human_needed(trace)
    assert_faults_visible(world, run_id, trace)
    assert_api_policy_and_platform(world, trace)
    assert_key_never_persisted(world, run_id)


def test_managed_loop_stops_on_blocking(make_world):
    plan = [step("generator_started", 1, "cli_not_found", repeat=True)]
    world = make_world(plan)
    work_dir = str(world.repo)

    foreground = meow(world, "run", "Add a thing", "-n", "--work-dir", work_dir)

    assert foreground.returncode == EXIT_BLOCKED
    blocked = contract_line(foreground.stderr, "MEOW_BLOCKED")
    assert (blocked["cause"], blocked["stop"]) == ("cli_not_found", "blocking")
    assert "MEOW_RETRY" not in foreground.stderr
    assert world.launches() == 1

    run_id = launch_background(world)
    trace = manage(world, run_id)
    time.sleep(4)  # longer than the supervisor's backoff plus a driver start
    final = status_of(world, run_id)
    assert (final["phase"], final["cause"], final["retryable"]) == (
        "failed",
        "cli_not_found",
        False,
    )
    assert final["next_command"] is None
    assert final["not_resumed_reason"] == "blocking: cli_not_found"
    assert not trace.actions
    assert world.launches() == BLOCKED_RUNS  # one driver each, no relaunch


def test_run_parked_before_its_worktree_exists_is_restarted(make_world):
    plan = [
        step("preparing", 0, "connection_reset", early=True),
        step("checking", 1, "complete"),
    ]
    world = make_world(plan, FAST_CONFIG)
    argv = ["run", "Add a thing", "--background", "--work-dir", str(world.repo)]
    done = meow(world, *argv)  # worktree mode, the default
    assert done.returncode == 0, done.stderr
    run_id = done.stdout.split("Background run: ")[1].split()[0]

    manage(world, run_id)

    record = RunStore(world.repo).load(run_id)
    assert record.phase in DONE
    phases = [item["phase"] for item in record.transitions]
    assert (phases.count("parked"), phases.count("resuming")) == (1, 1)
    # Restarted from the saved command (a worktree again), not resumed in place.
    assert [e["use_worktree"] for e in world.events("launch")] == [True, True]
    assert "before its worktree was prepared" in run_files(world, run_id)[".log"]


# R12: native sessions, supervised and queued runs, and the cases where nothing
# may be resumed.
STALE_CONFIG = "[run]\nstale_after = 100\n"
STALE_AGE = 500
MAX_RERUNS = 3
KILLS = 2
TOTAL_BUDGET = 2
BLOCKED_RUNS = 2  # the foreground and the background run
COMPLETE = [step("checking", 1, "complete")]


def deny_renames(monkeypatch, failures):
    """The next `failures` renames onto a run record are refused (Defender)."""
    from meow.common import atomic

    real, left = os.replace, [failures]

    def replace(source, target, *args, **kwargs):
        if left[0] > 0 and Path(str(target)).parent.name == "runs":
            left[0] -= 1
            denied = PermissionError(13, "Access is denied")
            denied.winerror = 5
            raise denied
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    for name, value in (("delay", 0.001), ("max_delay", 0.002)):
        monkeypatch.setitem(atomic.replace_with_retry.__kwdefaults__, name, value)


def rerun_while_transient(*argv):
    """A native session's rule: exit 75 means run the printed command again."""
    reruns = []
    code, out, err = run_native(*argv)
    while code == EXIT_RETRY and len(reruns) < MAX_RERUNS:
        payload = json_line(err)
        assert payload["retryable"] is True
        reruns.append(payload["command"])
        code, out, err = run_native(*payload["command"][2:])
    return code, out, reruns


def test_native_helper_transient_failures_are_rerun_until_the_run_completes(
    make_world, monkeypatch
):
    repo = make_world(COMPLETE, FAST_CONFIG).repo
    work = ["--work-dir", str(repo)]
    deny_renames(monkeypatch, 10)  # one helper call exhausts its rename retries

    created = ["checkpoint", "planned", "--request", "work", "--intent", "run", *work]
    code, out, reruns = rerun_while_transient(*created)

    assert (code, len(reruns)) == (0, 1)
    assert reruns[0][:4] == ["meow", "native", "checkpoint", "planned"]
    run_id = json_line(out)["run_id"]
    _review(repo, run_id)
    checks = AsyncMock(side_effect=[TimeoutError("final checks timed out"), []])
    with (
        patch("meow.native.native_state.run_final_checks", checks),
        patch("meow.native.native_state.deliver_verified_run"),
    ):
        code, out, reruns = rerun_while_transient("finalize", run_id, *work)

    assert (code, len(reruns)) == (0, 1)
    assert json_line(out)["complete"] is True
    record = RunStore(repo).load(run_id)
    assert record.phase == "complete"
    assert [item["phase"] for item in record.transitions].count("parked") == 1


def lose_native_session(repo):
    """A native run whose Claude session vanished: its heartbeat went stale."""
    from meow.native.native_state import checkpoint

    run_id = checkpoint(repo, repo, "generator_started", request="work", intent="run")[
        "run_id"
    ]
    RunStore(repo).update_results(run_id, heartbeat_at=time.time() - STALE_AGE)
    return run_id


def test_stale_native_run_is_parked_by_status_then_resumed(
    make_world, in_process, capsys
):
    repo = make_world(COMPLETE, STALE_CONFIG).repo
    run_id = lose_native_session(repo)
    work = ["--work-dir", str(repo)]

    _code, out, _err = run_cli(capsys, "status", run_id, "--json", *work)

    payload = json_line(out)
    assert (payload["phase"], payload["cause"]) == ("parked", "native_session_lost")
    assert payload["retryable"] is True
    assert "--repair" in payload["next_command"]
    code, _out, err = run_cli(capsys, *shlex.split(payload["next_command"])[1:])
    assert code == 0, err
    assert RunStore(repo).load(run_id).phase == "complete"


def test_stale_native_run_is_parked_and_resumed_by_resume_parked(
    make_world, in_process, capsys
):
    repo = make_world(COMPLETE, STALE_CONFIG).repo
    run_id = lose_native_session(repo)

    code, out, err = run_cli(capsys, "resume", "--parked", "--work-dir", str(repo))

    assert code == 0, err
    resumed = json_line(out)["resumed"]
    assert [(r["run_id"], r["phase"], r["exit"]) for r in resumed] == [
        (run_id, "complete", 0)
    ]
    record = RunStore(repo).load(run_id)
    assert record.park["cause"] == "native_session_lost"
    assert record.park["manager_resumes"] == 1


def only_run_id(world):
    [path] = (world.repo / ".meow" / "runs").glob("*.json")
    return path.stem


def test_supervised_run_killed_twice_is_resumed_to_completion(make_world, capsys):
    plan = [
        step("generator_started", 1, "sigkill"),
        step("reviewer_started", 1, "sigkill"),
        step("checking", 1, "complete"),
    ]
    world = make_world(plan, "[run]\nsupervise = true\nretry_backoff = [0]\n")

    code, _out, err = run_cli(
        capsys, "run", "Add a thing", "-n", "--work-dir", str(world.repo)
    )

    assert code == 0, err
    assert world.launches() == KILLS + 1
    run_id = only_run_id(world)
    record = RunStore(world.repo).load(run_id)
    phases = [item["phase"] for item in record.transitions]
    assert (record.phase, phases.count("parked")) == ("complete", KILLS)
    assert phases.count("resuming") == KILLS
    assert run_files(world, run_id)[".error.log"].count("SIGKILL during") == KILLS


def test_supervised_run_budget_exhaustion_ends_exit_1_with_the_budget_reason(
    make_world, capsys
):
    plan = [step("generator_started", n, "sigkill") for n in range(1, 5)]
    config = (
        "[run]\nsupervise = true\nretry_backoff = [0]\n"
        f"max_total_resumes = {TOTAL_BUDGET}\n"
    )
    world = make_world(plan, config)
    work = ["--work-dir", str(world.repo)]

    code, _out, err = run_cli(capsys, "run", "Add a thing", "-n", *work)

    assert code == EXIT_BLOCKED
    blocked = contract_line(err, "MEOW_BLOCKED")
    assert blocked["stop"] == "budget_exhausted"
    assert "max_total_resumes reached" in blocked["message"]
    assert world.launches() == TOTAL_BUDGET + 1  # not an endless loop
    _code, out, _err = run_cli(capsys, "status", blocked["run_id"], "--json", *work)
    payload = json_line(out)
    assert (payload["retryable"], payload["next_command"]) == (False, None)
    assert payload["not_resumed_reason"] == "max_total_resumes reached"


def test_queue_worker_crash_is_requeued_and_rerun(make_world):
    plan = [step("generator_started", 1, "sigkill"), step("checking", 1, "complete")]
    world = make_world(plan, FAST_CONFIG)
    work = ["--work-dir", str(world.repo)]
    assert meow(world, "queue", "Add a thing", *work).returncode == 0

    crashed = meow(world, "queue", *work)  # the worker is killed with its run

    assert crashed.returncode == EXIT_KILLED
    sweep = meow(world, "resume", "--parked", *work)  # the managing loop's sweep
    queue = QueueStore(world.repo)
    [item] = queue.list()
    assert sweep.returncode == EXIT_RETRY
    assert json_line(sweep.stdout)["requeued"] == [item.id]
    assert item.status == "pending"
    assert meow(world, "queue", *work).returncode == 0
    [item] = queue.list()
    assert (item.status, item.attempts) == ("completed", 2)
    final = meow(world, "resume", "--parked", *work)
    assert (final.returncode, json_line(final.stdout)["resumed"]) == (0, [])
    assert RunStore(world.repo).load(item.run_id).phase == "complete"
    assert world.launches() == KILLS


def live_queue_run(repo):
    """A queue item whose driver (our parent process) is alive."""
    store, queue = RunStore(repo), QueueStore(repo)
    item = queue.enqueue("Add a thing")
    queue.claim(item.id)
    run = store.create(
        source="queue", request="q", repo=repo, worktree=repo, branch="dev"
    )
    store.transition(run.id, "generator_started")
    live = store.load(run.id)
    live.driver = {
        "pid": os.getppid(),
        "start_identity": process_identity(os.getppid()),
    }
    store._write(live)
    queue.update(item.id, run_id=run.id)
    return run


def test_queue_run_with_a_live_driver_is_not_resumed(tmp_path, capsys):
    repo = git_repo(tmp_path)
    work = ["--work-dir", str(repo)]
    run = live_queue_run(repo)
    lock = QueueWorkerLock(QueueStore(repo).directory)
    assert lock.acquire()
    try:
        sweep = run_cli(capsys, "resume", "--parked", *work)
        second_worker = run_cli(capsys, "queue", *work)
        refused = run_cli(capsys, "resume", run.id, "--continue", *work)
        status = run_cli(capsys, "status", run.id, "--json", *work)
    finally:
        lock.release()

    assert (sweep[0], json_line(sweep[1])["requeued"]) == (0, [])
    assert "already running" in second_worker[1]
    assert refused[0] == EXIT_BLOCKED
    assert f"live process PID {os.getppid()}" in refused[2]  # the reason
    assert json_line(status[1])["settled"] is False
    assert RunStore(repo).load(run.id).phase == "generator_started"
    assert QueueStore(repo).list()[0].status == "running"


def run_status(capsys, repo, run_id):
    _code, out, _err = run_cli(
        capsys, "status", run_id, "--json", "--work-dir", str(repo)
    )
    return json_line(out)


def test_blocking_cause_is_not_resumed_and_the_reason_is_reported(
    make_world, in_process, capsys
):
    world = make_world([step("generator_started", 1, "auth_401")], FAST_CONFIG)
    work = ["--work-dir", str(world.repo)]

    code, _out, err = run_cli(capsys, "run", "Add a thing", "-n", *work)

    assert code == EXIT_BLOCKED
    blocked = contract_line(err, "MEOW_BLOCKED")
    assert (blocked["cause"], blocked["stop"]) == ("auth", "blocking")
    payload = run_status(capsys, world.repo, blocked["run_id"])
    assert (payload["retryable"], payload["next_command"]) == (False, None)
    assert payload["not_resumed_reason"] == "blocking: auth"
    sweep = run_cli(capsys, "resume", "--parked", *work)
    assert (sweep[0], json_line(sweep[1])["resumed"]) == (0, [])
    assert world.launches() == 1


def test_non_resumable_runs_are_skipped_with_their_reason(tmp_path, capsys):
    repo = git_repo(tmp_path)
    store = RunStore(repo)
    queued = store.create(
        source="queue", request="q", repo=repo, worktree=repo, branch="dev"
    )
    planned = store.create(
        source="native", request="p", repo=repo, worktree=repo, branch="dev"
    )
    store.update_results(planned.id, intent="plan")
    store.transition(planned.id, "planning")
    for run in (queued, planned):
        store.park(run.id, "crash", "transient", "error")

    reasons = {
        run.id: run_status(capsys, repo, run.id)["not_resumed_reason"]
        for run in (queued, planned)
    }
    assert reasons == {
        queued.id: "queue runs are rerun, not resumed",
        planned.id: "plan-only native run",
    }
    code, out, _err = run_cli(capsys, "resume", "--parked", "--work-dir", str(repo))
    summary = json_line(out)
    assert (code, summary["resumed"]) == (0, [])
    assert {e["run_id"]: e["reason"] for e in summary["skipped"]} == reasons
    assert not any(e["retryable"] for e in summary["skipped"])


def test_repeated_failure_without_progress_stops_and_names_what_repeated(
    make_world, in_process, capsys
):
    plan = [step("generator_started", 1, "connection_reset", repeat=True)]
    world = make_world(plan, FAST_CONFIG)
    work = ["--work-dir", str(world.repo)]
    code, _out, err = run_cli(capsys, "run", "Add a thing", "-n", *work)
    run_id = contract_line(err, "MEOW_RETRY")["run_id"]
    attempts = []

    for _ in range(MAX_ACTIONS):
        payload = run_status(capsys, world.repo, run_id)
        attempts.append(payload["attempts"])
        if not payload["retryable"]:
            break
        code, _out, err = run_cli(capsys, *shlex.split(payload["next_command"])[1:])

    assert (code, contract_line(err, "MEOW_BLOCKED")["stop"]) == (
        EXIT_BLOCKED,
        "no_progress",
    )
    # The first resume re-enters `preparing` at the saved round, which counts as
    # one new step; after that the same failure at the same step adds attempts.
    assert attempts == [1, *range(1, AUTO_RETRIES + 2)]
    assert world.launches() == AUTO_RETRIES + 2  # no relaunch past the budget
    assert payload["cause"] == payload["not_resumed_reason"] == "no_progress"
    assert payload["next_command"] is None
    assert "No progress" in payload["last_failure"]
    assert "Connection reset" in payload["last_failure"]


def test_managed_resumes_budget_stops_the_sweeps_with_the_reason(
    make_world, in_process, capsys
):
    plan = [step("generator_started", n, "connection_reset") for n in range(1, 9)]
    world = make_world(plan, FAST_CONFIG)
    work = ["--work-dir", str(world.repo)]
    _code, _out, err = run_cli(capsys, "run", "Add a thing", "-n", *work)
    run_id = contract_line(err, "MEOW_RETRY")["run_id"]

    for _ in range(MANAGED_RESUMES):  # each sweep makes progress, then fails
        code, out, _err = run_cli(capsys, "resume", "--parked", *work)
        assert (code, json_line(out)["resumed"][0]["phase"]) == (EXIT_RETRY, "parked")
    code, out, _err = run_cli(capsys, "resume", "--parked", *work)

    summary = json_line(out)
    assert (code, summary["stop"], summary["resumed"]) == (
        EXIT_BLOCKED,
        "no_progress",
        [],
    )
    assert [e["reason"] for e in summary["skipped"]] == ["managed_resumes reached"]
    assert world.launches() == MANAGED_RESUMES + 1
    payload = run_status(capsys, world.repo, run_id)
    assert (payload["retryable"], payload["budget_left"]) == (False, 0)
    assert payload["not_resumed_reason"] == "managed_resumes reached"

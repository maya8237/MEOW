"""Skills and docs must teach the managed continuation loop and stay short."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = [
    "skills/run/SKILL.md",
    "skills/review/SKILL.md",
    "skills/plan/SKILL.md",
    "skills/schedule/SKILL.md",
    "skills/_shared/native-mode.md",
]
DOCS = [
    "docs/RUNS.md",
    "docs/WORKTREE_SETUP.md",
    "docs/INTEGRATIONS.md",
    "docs/CLI.md",
]
MAX_LINES = 200
TERMS = ["meow status", "--json", "75", "next_command", "MEOW_BLOCKED"]


@pytest.mark.parametrize("path", SKILLS)
def test_skill_teaches_continuation_contract(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    for term in TERMS:
        assert term in text, f"{path} lacks {term!r}"


@pytest.mark.parametrize("path", SKILLS + DOCS + ["skills/run/references/native.md"])
def test_files_stay_under_200_lines(path):
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    assert len(lines) < MAX_LINES, f"{path} has {len(lines)} lines"


def test_run_skill_launch_and_watch_rules():
    text = (ROOT / "skills/run/SKILL.md").read_text(encoding="utf-8")
    for term in (
        "--background",
        "*/7 * * * *",
        "retryable",
        "budget_left",
        "managed_resumes",
    ):
        assert term in text


def test_native_and_run_share_exit_75_wording():
    run = (ROOT / "skills/run/SKILL.md").read_text(encoding="utf-8")
    native = (ROOT / "skills/_shared/native-mode.md").read_text(encoding="utf-8")
    sentence = "A command exiting `75` (or printing `MEOW_RETRY`)"
    assert sentence in run and sentence in native
    assert "meow resume --parked" in (ROOT / "skills/schedule/SKILL.md").read_text(
        encoding="utf-8"
    )


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def _flat(path):
    return " ".join(_read(path).split())


@pytest.mark.parametrize(
    "path", ["skills/run/SKILL.md", "skills/_shared/native-mode.md", "docs/RUNS.md"]
)
def test_tick_acts_only_on_settled_runs(path):
    text = _flat(path)
    assert (
        "act only when `settled` is true" in text
        or "acting only when `settled`" in text
    )
    assert "retryable: false" in text


@pytest.mark.parametrize(
    "path", ["skills/run/SKILL.md", "skills/_shared/native-mode.md"]
)
def test_shared_tick_and_retry_wording_is_identical(path):
    text = _flat(path)
    for start, end in (
        ("Each tick:", "against `managed_resumes`."),
        ("A command exiting `75`", "detached or leave it to the hourly OS job."),
    ):
        block = text[text.index(start) : text.index(end) + len(end)]
        run = _flat("skills/run/SKILL.md")
        assert block in run
        assert block in _flat("skills/_shared/native-mode.md")


def test_resumes_are_detached_and_crash_checkpoint_has_run_id():
    native = _flat("skills/_shared/native-mode.md")
    assert "checkpoint parked --run-id ID --failure" in native
    assert "detached" in native and "never the foreground" in native
    assert "`no_progress`" in native and "Exit 3" in native


@pytest.mark.parametrize(
    "path",
    [
        "skills/run/SKILL.md",
        "skills/_shared/native-mode.md",
        "skills/review/SKILL.md",
        "skills/plan/SKILL.md",
    ],
)
def test_exit_75_paragraph_runs_next_command_detached(path):
    text = _flat(path)
    start = text.index("exiting `75`")
    assert "detached" in text[start : start + 400]


@pytest.mark.parametrize(
    "path", ["skills/run/SKILL.md", "skills/_shared/native-mode.md"]
)
def test_orchestrator_status_guidance_matches_the_json(path):
    text = " ".join((ROOT / path).read_text(encoding="utf-8").split())
    assert "Orchestrator IDs report the same fields" in text
    assert "only `phase`" not in text

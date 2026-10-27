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

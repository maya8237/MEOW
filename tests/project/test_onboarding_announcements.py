"""Every onboarding gap is either released, announced, or deliberately quiet."""

import tomllib
from pathlib import Path

from meow.installer.maintenance import _version_parts
from meow.project.announcements import ANNOUNCED, NOT_ANNOUNCED
from meow.project.onboarding import _GAPS

# `_GAPS` keys in the 0.1.18 release. Frozen: never derive it from `_GAPS`.
BASELINE = frozenset({
    "agent_skills",
    "build",
    "custom",
    "github",
    "gitlab",
    "jira",
    "permissions",
    "tester_browser",
    "tester_mcp",
    "tester_tests",
    "worktree_setup",
})
PROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_every_onboarding_gap_has_one_announcement_decision():
    places = (BASELINE, set(ANNOUNCED), NOT_ANNOUNCED)
    undecided = sorted(set(_GAPS) - set().union(*places))
    assert not undecided, (
        f"New onboarding gaps {undecided}: if /meow:onboard walks through them, "
        'add them to ANNOUNCED with "unreleased" and a one-line summary in '
        "src/meow/project/announcements.py; otherwise add them to NOT_ANNOUNCED."
    )
    for key in _GAPS:
        assert sum(key in place for place in places) == 1, key


def test_announcements_name_current_gaps():
    stale = sorted((set(ANNOUNCED) | NOT_ANNOUNCED) - set(_GAPS))
    assert not stale, f"Renamed or removed gaps still listed: {stale}"


def test_announced_versions_are_unreleased_or_already_released():
    current = tomllib.loads(PROJECT.read_text())["project"]["version"]
    for key, (since, summary) in ANNOUNCED.items():
        assert summary.strip(), key
        if since != "unreleased":
            assert _version_parts(since) <= _version_parts(current), key

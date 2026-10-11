from pathlib import Path

import pytest

from meow.project.permissions import parse_policy


def _policy(*rules):
    return parse_policy({"rule": list(rules)}, roles={"custom"})


def test_write_rule_also_covers_edit(tmp_path: Path):
    role = _policy({
        "role": "generator",
        "tool": "Write",
        "action": "deny",
        "path": ".env",
    }).for_role("generator")

    assert role.decision("Edit", {"file_path": ".env"}, project_root=tmp_path) == "deny"
    assert (
        role.decision("Edit", {"file_path": "src/a.py"}, project_root=tmp_path) is None
    )


def test_read_rule_also_covers_grep_and_glob(tmp_path: Path):
    role = _policy({
        "role": "reviewer",
        "tool": "Read",
        "action": "deny",
        "path": "secrets",
    }).for_role("reviewer")

    assert role.decision("Grep", {"path": "secrets"}, project_root=tmp_path) == "deny"
    assert (
        role.decision("Glob", {"path": "secrets/keys"}, project_root=tmp_path) == "deny"
    )
    assert role.decision("Grep", {"path": "src"}, project_root=tmp_path) is None


def test_directory_search_at_root_reaches_protected_path(tmp_path: Path):
    role = _policy({
        "role": "reviewer",
        "tool": "Read",
        "action": "deny",
        "path": "secrets",
    }).for_role("reviewer")

    assert role.decision("Grep", {"path": "."}, project_root=tmp_path) == "deny"
    assert role.decision("Glob", {}, project_root=tmp_path) == "deny"


@pytest.mark.parametrize("escape", ["../outside.txt", "/etc/passwd"])
def test_paths_escaping_the_project_are_denied(tmp_path: Path, escape: str):
    role = _policy({
        "role": "reviewer",
        "tool": "Read",
        "action": "deny",
        "path": "secrets",
    }).for_role("reviewer")

    assert role.decision("Read", {"file_path": escape}, project_root=tmp_path) == "deny"


def test_gated_tools_cover_the_whole_file_family():
    role = _policy({
        "role": "generator",
        "tool": "Read",
        "action": "deny",
        "path": "secrets",
    }).for_role("generator")

    gated = role.gated_tools()
    assert {"Read", "Grep", "Glob", "Bash", "Agent"} <= gated

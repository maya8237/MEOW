import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_CONFIG_NAME = "." + "harness.toml"


def _git_ignores(path: str) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", path],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0
    )


def test_project_shared_config_is_trackable_but_local_state_is_ignored():
    assert not _git_ignores(".meow/config.toml")
    assert _git_ignores(".meow/config.local.toml")
    assert _git_ignores(".meow/runs/example.json")


def test_repository_does_not_reintroduce_removed_backend_references():
    removed_backend = "open" + "hands"
    tracked = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    offending = []
    for name in tracked:
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            if removed_backend in path.read_text(encoding="utf-8").lower():
                offending.append(name)
        except (OSError, UnicodeError):
            continue

    assert offending == []


def test_legacy_config_references_are_confined_to_migration():
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    offending = []
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or "migration" in path.parts:
            continue
        try:
            if LEGACY_CONFIG_NAME in path.read_text(encoding="utf-8"):
                offending.append(name)
        except (OSError, UnicodeError):
            continue

    assert offending == []

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _git_ignores(path: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=ROOT,
        check=False,
    ).returncode == 0


def test_onboarding_is_current_setup_and_repairs_gitignore_itself():
    text = (ROOT / "skills" / "onboard" / "SKILL.md").read_text(encoding="utf-8")

    assert "current or clean" in text
    assert ".meow/*" in text
    assert "!.meow/" in text
    assert "!.meow/config.toml" in text
    assert "independently" in text.lower()
    assert ".meow/config.local.toml" in text
    assert ".harness.toml" not in text
    assert "knowledge-audit" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "disposable" in text


def test_migration_is_separate_and_repairs_gitignore_itself():
    text = (ROOT / "skills" / "migration" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "legacy" in text.lower()
    assert ".harness.toml" in text
    assert ".meow/*" in text
    assert "!.meow/" in text
    assert "!.meow/config.toml" in text
    assert "independently" in text.lower()
    assert "config.local.toml" in text


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

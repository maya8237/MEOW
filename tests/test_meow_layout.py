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

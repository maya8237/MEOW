import pytest

from meow.project.migration import migrate_legacy_config
from meow.project.onboarding import onboard_project


@pytest.mark.parametrize(
    "private_config",
    [
        '[jira.mcp]\ncommand = "private-launcher"\n',
        '[github.mcp.env]\nTOKEN = "private-value"\n',
        '[agent_skills]\ndefault = ["personal-skill"]\n',
        '[custom]\nskills = ["C:/private/skills"]\n',
        'api_key = "private-value"\n',
    ],
)
def test_private_legacy_config_requires_explicit_migration(tmp_path, private_config):
    legacy = tmp_path / ".harness.toml"
    legacy.write_text(private_config, encoding="utf-8")

    with pytest.raises(ValueError, match="/meow:migration") as error:
        migrate_legacy_config(tmp_path)

    assert "private-value" not in str(error.value)
    assert not (tmp_path / ".meow/config.toml").exists()
    assert legacy.read_text(encoding="utf-8") == private_config


def test_onboarding_reports_unsafe_legacy_migration_without_copying(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".harness.toml").write_text(
        '[jira.mcp.env]\nTOKEN = "private-value"\n', encoding="utf-8"
    )

    report = onboard_project(tmp_path)

    assert report.error and "/meow:migration" in report.error
    assert "private-value" not in report.error
    assert not (tmp_path / ".meow/config.toml").exists()


def test_migrates_legacy_config_without_docs_dir(tmp_path):
    (tmp_path / ".harness.toml").write_text(
        'docs_dir = "docs/plans"\nmax_rounds = 3\n\n[[lint]]\ncommand = "x"\n'
        'docs_dir = "kept-in-table"\n',
        encoding="utf-8",
    )

    assert migrate_legacy_config(tmp_path) == ".meow/config.toml"

    text = (tmp_path / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert "max_rounds = 3" in text
    assert 'docs_dir = "docs/plans"' not in text
    assert 'docs_dir = "kept-in-table"' in text
    assert (tmp_path / ".harness.toml").is_file()


def test_never_overwrites_existing_config(tmp_path):
    (tmp_path / ".harness.toml").write_text("max_rounds = 3\n", encoding="utf-8")
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("max_rounds = 9\n")

    assert migrate_legacy_config(tmp_path) is None
    assert "9" in (tmp_path / ".meow" / "config.toml").read_text()


def test_onboarding_migrates_legacy_project(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".harness.toml").write_text("max_rounds = 3\n", encoding="utf-8")

    report = onboard_project(tmp_path)

    assert report.error is None
    assert ".meow/config.toml" in report.files
    assert "max_rounds = 3" in (tmp_path / ".meow/config.toml").read_text()

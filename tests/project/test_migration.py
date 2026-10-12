from meow.project.migration import migrate_legacy_config
from meow.project.onboarding import onboard_project


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

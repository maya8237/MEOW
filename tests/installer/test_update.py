import subprocess
import sys

import pytest

from meow.cli.parser import _build_arg_parser


def test_update_flag_needs_no_project_and_cannot_combine_with_a_command():
    assert _build_arg_parser().parse_args(["--update"]).update
    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["--update", "status", "run-123"])


@pytest.mark.parametrize(
    "case",
    [
        ("0.1.2", "0.1.3", True),
        ("0.1.3", "0.1.3", False),
        ("0.2.0", "0.1.3", False),
        ("0.1.9", "0.1.10", True),
    ],
)
def test_update_only_installs_newer_versions(tmp_path, monkeypatch, case):
    from meow.installer import maintenance

    current, latest, updates = case
    monkeypatch.setattr(maintenance, "version", lambda name: current)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{current}"\n')
    monkeypatch.setattr(maintenance, "installed_checkout", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "latest_version", lambda: latest)
    calls = []

    def pull(repo):
        calls.append("update")
        (repo / "pyproject.toml").write_text(f'[project]\nversion = "{latest}"\n')

    monkeypatch.setattr(maintenance, "update_checkout", pull)
    monkeypatch.setattr(
        maintenance, "install_checkout", lambda repo: calls.append("install")
    )
    monkeypatch.setattr(
        maintenance, "run_setup", lambda repo: calls.append("setup") or 0
    )

    assert maintenance.update() == 0
    assert calls == (["update", "install", "setup"] if updates else [])


def test_failed_update_does_not_install_or_run_setup(tmp_path, monkeypatch):
    from meow.installer import maintenance

    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.2"\n')
    monkeypatch.setattr(maintenance, "installed_checkout", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "latest_version", lambda: "0.1.3")

    def fail(repo):
        raise RuntimeError("commit or stash")

    monkeypatch.setattr(maintenance, "update_checkout", fail)
    with pytest.raises(RuntimeError, match="commit or stash"):
        maintenance.update()


def test_update_refuses_to_report_success_if_pull_did_not_reach_release(
    tmp_path, monkeypatch
):
    from meow.installer import maintenance

    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.2"\n')
    monkeypatch.setattr(maintenance, "installed_checkout", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "latest_version", lambda: "0.1.3")
    monkeypatch.setattr(maintenance, "update_checkout", lambda repo: None)
    monkeypatch.setattr(
        maintenance,
        "install_checkout",
        lambda repo: pytest.fail("stale release installed"),
    )
    with pytest.raises(RuntimeError, match=r"0\.1\.3"):
        maintenance.update()


@pytest.mark.parametrize("failure", ["install", "setup"])
def test_update_retries_unfinished_installation_or_setup(  # ruff: ignore[too-many-statements]
    tmp_path, monkeypatch, failure
):
    from meow.installer import maintenance

    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "0.1.2"\n')
    monkeypatch.setattr(maintenance, "installed_checkout", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "latest_version", lambda: "0.1.3")
    monkeypatch.setattr(maintenance, "version", lambda name: "0.1.3")
    calls = []

    def pull(repo):
        calls.append("pull")
        project.write_text('[project]\nversion = "0.1.3"\n')

    def install(repo):
        calls.append("install")
        if failure == "install" and calls.count("install") == 1:
            raise RuntimeError("pip failed")

    def setup(repo):
        calls.append("setup")
        return int(failure == "setup" and calls.count("setup") == 1)

    monkeypatch.setattr(maintenance, "update_checkout", pull)
    monkeypatch.setattr(maintenance, "install_checkout", install)
    monkeypatch.setattr(maintenance, "run_setup", setup)
    if failure == "install":
        with pytest.raises(RuntimeError, match="pip failed"):
            maintenance.update()
    else:
        assert maintenance.update() == 1
    assert maintenance.update() == 0
    assert calls == (
        ["pull", "install", "install", "setup"]
        if failure == "install"
        else ["pull", "install", "setup", "install", "setup"]
    )


@pytest.mark.parametrize("case", [("0.1.3", True), ("0.1.2", False), ("0.1.1", False)])
def test_update_warning_is_on_stderr_without_updating(
    tmp_path, monkeypatch, capsys, case
):
    from meow.installer import maintenance

    latest, warns = case
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.2"\n')
    monkeypatch.setattr(maintenance, "installed_checkout", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "latest_version", lambda **kwargs: latest)
    maintenance.check_for_updates()
    output = capsys.readouterr()
    assert not output.out
    assert ("meow --update" in output.err) == warns


def test_update_check_is_quiet_when_offline(monkeypatch, capsys):
    from meow.installer import maintenance

    def offline(**kwargs):
        raise OSError("offline")

    monkeypatch.setattr(maintenance, "latest_version", offline)
    maintenance.check_for_updates()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "argv, checks",
    [
        (["run", "--help"], True),
        (["p", "--help"], True),
        (["--version"], True),
        (["native", "--help"], False),
        (["_worker", "--help"], False),
    ],
)
def test_public_cli_calls_check_versions_but_helpers_do_not(monkeypatch, argv, checks):
    from meow.cli import cli
    from meow.installer import maintenance

    calls = []
    monkeypatch.setattr(maintenance, "check_for_updates", lambda: calls.append("check"))
    with pytest.raises(SystemExit):
        cli.cli_main(argv)
    assert bool(calls) == checks


def test_install_uses_running_python_and_preserves_paths_with_spaces(
    tmp_path, monkeypatch
):
    from meow.installer import maintenance

    commands = []
    flags = []

    def run(args, **kwargs):
        commands.append(args)
        flags.append(kwargs.get("creationflags", 0))

    monkeypatch.setattr(subprocess, "run", run)
    maintenance.install_checkout(tmp_path / "checkout with spaces")
    assert commands == [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-e",
            str(tmp_path / "checkout with spaces"),
        ]
    ]
    assert flags == [0x08000000 if sys.platform == "win32" else 0]


def test_update_fast_forwards_main_and_refuses_dirty_or_feature_checkouts(tmp_path):  # ruff: ignore[too-many-statements]
    from meow.installer import maintenance

    def git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "main")
    git(remote, "config", "user.email", "test@example.com")
    git(remote, "config", "user.name", "Test")
    (remote / "version").write_text("old")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "old")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(remote), str(checkout))
    (remote / "version").write_text("new")
    git(remote, "commit", "-am", "new")
    maintenance.update_checkout(checkout)
    assert (checkout / "version").read_text() == "new"
    (checkout / "version").write_text("local")
    with pytest.raises(RuntimeError, match="commit or stash"):
        maintenance.update_checkout(checkout)
    assert (checkout / "version").read_text() == "local"
    git(checkout, "restore", "version")
    git(checkout, "checkout", "-b", "feature")
    with pytest.raises(RuntimeError, match="main"):
        maintenance.update_checkout(checkout)

import pytest

from meow.installer import maintenance, releases


@pytest.mark.parametrize(
    "current,latest,installs",
    [
        ("0.1.2", "0.2.0", ["0.2.0"]),
        ("0.2.0", "0.2.0", []),
        ("0.3.0", "0.2.0", []),
        ("0.1.9", "0.1.10", ["0.1.10"]),
    ],
)
def test_update_selects_newer_published_release(monkeypatch, current, latest, installs):
    monkeypatch.setattr(maintenance, "version", lambda name: current)
    monkeypatch.setattr(maintenance, "latest_release", lambda: (latest, "0" * 64))
    installed = []
    monkeypatch.setattr(
        releases, "install_release", lambda tag, digest: installed.append(tag)
    )
    assert maintenance.update() == 0
    assert installed == installs


def test_failed_download_does_not_report_success(monkeypatch, capsys):
    monkeypatch.setattr(maintenance, "version", lambda name: "0.1.2")
    monkeypatch.setattr(maintenance, "latest_release", lambda: ("0.2.0", "0" * 64))

    def fail(tag, digest):
        raise OSError("offline")

    monkeypatch.setattr(releases, "install_release", fail)
    with pytest.raises(OSError, match="offline"):
        maintenance.update()
    assert "successfully" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "latest,warns", [("0.2.0", True), ("0.1.2", False), ("0.1.1", False)]
)
def test_warning_uses_release_version_and_stderr(monkeypatch, capsys, latest, warns):
    monkeypatch.setattr(maintenance, "version", lambda name: "0.1.2")
    monkeypatch.setattr(maintenance, "latest_version", lambda **kwargs: latest)
    maintenance.check_for_updates()
    out = capsys.readouterr()
    assert not out.out
    assert ("meow --update" in out.err) == warns


def test_offline_check_is_quiet(capsys):
    maintenance.check_for_updates()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "argv,checks",
    [
        (["run", "--help"], True),
        (["--version"], True),
        (["native", "--help"], True),
        (["_worker", "--help"], False),
    ],
)
def test_only_public_commands_check_versions(monkeypatch, argv, checks):
    from meow.cli.cli import cli_main

    calls = []
    monkeypatch.setattr(maintenance, "check_for_updates", lambda: calls.append(True))
    with pytest.raises(SystemExit):
        cli_main(argv)
    assert bool(calls) == checks


def test_native_update_warning_preserves_json_stdout(monkeypatch, capsys):
    import json

    from meow.cli import cli

    monkeypatch.setattr(maintenance, "version", lambda name: "0.1.2")
    monkeypatch.setattr(maintenance, "latest_version", lambda **kwargs: "0.2.0")
    monkeypatch.setattr(cli, "run_native", lambda args: print('{"status": "ok"}'))

    cli.cli_main(["native", "latest-plan"])

    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "ok"}
    assert "meow --update" in output.err

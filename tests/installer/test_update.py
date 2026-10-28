from pathlib import Path

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


@pytest.mark.parametrize("interactive", [False, True])
def test_update_never_offers_optional_setup(monkeypatch, tmp_path, interactive):
    monkeypatch.setattr(maintenance, "version", lambda _: "0.1.2")
    monkeypatch.setattr(maintenance, "latest_release", lambda: ("0.2.0", "0" * 64))
    monkeypatch.setattr(releases, "install_release", lambda *_: tmp_path)
    monkeypatch.setattr(maintenance, "announce_onboarding", lambda *_: None)
    monkeypatch.setattr(maintenance.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(
        maintenance, "offer_bun_store", lambda *_: pytest.fail("optional setup offered")
    )
    assert maintenance.update() == 0


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
    monkeypatch.setattr(cli, "run_native", lambda args, *_: print('{"status": "ok"}'))

    cli.cli_main(["native", "latest-plan"])

    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "ok"}
    assert "meow --update" in output.err


@pytest.fixture(scope="module")
def release_venv(tmp_path_factory):
    """A real release venv whose announcement table differs from this checkout."""
    import subprocess
    import sys

    release = tmp_path_factory.mktemp("versions") / "0.1.21"
    runtime = release / "runtime"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(runtime)], check=True
    )
    purelib = subprocess.run(
        [
            str(releases.release_python(release)),
            "-c",
            "import sysconfig;print(sysconfig.get_path('purelib'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    package = Path(purelib) / "meow" / "project"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    return release, package / "announcements.py"


def _ship(table_file, entries=None, body=None):
    import json

    table = json.dumps({
        key: {"since": since, "summary": key.title()} for key, since in entries or []
    })
    table_file.write_text(body or f"print({table!r})\n")


@pytest.fixture
def released(monkeypatch, release_venv):
    monkeypatch.setattr(maintenance, "_local_install", lambda: False)
    # The child must read the synthetic release, not the parent's worktree.
    monkeypatch.delenv("PYTHONPATH", raising=False)
    return release_venv


def test_update_announces_options_from_the_new_release(monkeypatch, capsys, released):
    release, table = released
    _ship(table, [("seen", "0.1.18"), ("next", "0.1.20"), ("first", "0.1.19")])
    monkeypatch.setattr(maintenance, "version", lambda name: "0.1.18")
    monkeypatch.setattr(maintenance, "latest_release", lambda: ("0.1.21", "0" * 64))
    monkeypatch.setattr(releases, "install_release", lambda tag, digest: release)

    assert maintenance.update() == 0

    out = capsys.readouterr().out
    assert out.endswith(
        "MEOW updated successfully to 0.1.21.\n"
        "New onboarding options since MEOW 0.1.18:\n"
        "  - First\n"
        "  - Next\n"
        "Run /meow:onboard in each project to review them.\n"
    )


def test_announcement_skips_unreleased_future_and_malformed_tags(capsys, released):
    release, table = released
    _ship(
        table,
        [
            ("draft", "unreleased"),
            ("later", "0.1.22"),
            ("bad", "1.x"),
            ("ok", "0.1.21"),
        ],
    )
    maintenance.announce_onboarding("0.1.20", "0.1.21", release)
    assert capsys.readouterr().out == (
        "New onboarding options since MEOW 0.1.20:\n  - Ok\n"
        "Run /meow:onboard in each project to review them.\n"
    )


def test_announcement_caps_long_lists(capsys, released):
    release, table = released
    _ship(table, [(f"f{index}", "0.1.20") for index in range(8)])
    maintenance.announce_onboarding("0.1.18", "0.1.21", release)
    lines = capsys.readouterr().out.splitlines()
    assert lines[1:6] == [f"  - F{index}" for index in range(5)]
    assert lines[6:] == [
        "  ... and 3 more",
        "Run /meow:onboard in each project to review them.",
    ]


@pytest.mark.parametrize(
    "body", ["print('{}')\n", "raise SystemExit(3)\n", "print('not json')\n"]
)
def test_announcement_failures_print_nothing(capsys, released, body):
    release, table = released
    _ship(table, body=body)
    maintenance.announce_onboarding("0.1.18", "0.1.21", release)
    assert capsys.readouterr().out == ""


def test_missing_release_runtime_prints_nothing(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(maintenance, "_local_install", lambda: False)
    maintenance.announce_onboarding("0.1.18", "0.1.21", tmp_path / "gone")
    assert capsys.readouterr().out == ""


def test_checkout_installs_skip_announcements():
    assert maintenance._local_install()


def test_shipped_table_prints_json():
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "meow.project.announcements"],
        check=True,
        capture_output=True,
        text=True,
    )
    table = json.loads(result.stdout)
    assert table
    assert all(set(entry) == {"since", "summary"} for entry in table.values())

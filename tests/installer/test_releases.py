import io
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from meow.cli.parser import _build_arg_parser
from meow.installer import maintenance


@pytest.mark.parametrize("tag", ["1.2.3", "2.0.0", "2.1.0b1"])
def test_release_tag_sets_package_and_plugin_versions(tmp_path, monkeypatch, tag):
    root = Path(__file__).resolve().parents[2]
    script = runpy.run_path(str(root / "scripts/stamp_release.py"))
    monkeypatch.setitem(script["check_version"].__globals__, "ROOT", tmp_path)
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "meow"\nversion = "0.0.1"\n')
    manifests = tmp_path / ".claude-plugin"
    manifests.mkdir()
    (manifests / "plugin.json").write_text('{"name":"meow","version":"0.0.1"}')
    (manifests / "marketplace.json").write_text(
        '{"plugins":[{"name":"meow","version":"0.0.2"}]}'
    )

    assert script["check_version"](tag) == tag
    assert f'version = "{tag}"' in project.read_text()
    assert json.loads((manifests / "plugin.json").read_text())["version"] == tag
    assert (
        json.loads((manifests / "marketplace.json").read_text())["plugins"][0][
            "version"
        ]
        == tag
    )


def _stamp_script(tmp_path, monkeypatch, table):
    root = Path(__file__).resolve().parents[2]
    script = runpy.run_path(str(root / "scripts/stamp_release.py"))
    monkeypatch.setitem(script["stamp_announcements"].__globals__, "ROOT", tmp_path)
    path = tmp_path / script["ANNOUNCEMENTS"]
    path.parent.mkdir(parents=True)
    path.write_text(f"ANNOUNCED: dict[str, tuple[str, str]] = {table}\n")
    return script, path


def _table(path):
    namespace = {}
    exec(path.read_text(), namespace)
    return {key: since for key, (since, _) in namespace["ANNOUNCED"].items()}


def test_release_stamps_unreleased_announcements(tmp_path, monkeypatch):
    script, path = _stamp_script(
        tmp_path,
        monkeypatch,
        '{\n    "old": ("0.1.18", "Old"),\n    "new": (\n        "unreleased",\n'
        '        "New",\n    ),\n    "next": ("unreleased", "Next"),\n}',
    )
    assert script["unreleased"]() == ["new", "next"]
    assert script["stamp_announcements"]("0.1.19", {"new"}) == ["new"]
    assert _table(path) == {"old": "0.1.18", "new": "0.1.19", "next": "unreleased"}
    assert script["stamp_announcements"]("0.2.0") == ["next"]
    assert _table(path)["next"] == "0.2.0"


def test_release_rejects_announcements_after_its_version(tmp_path, monkeypatch):
    script, path = _stamp_script(
        tmp_path, monkeypatch, '{"early": ("unreleased", "E"), "x": ("0.2.0", "X")}'
    )
    with pytest.raises(ValueError, match=r"x is announced for 0\.2\.0"):
        script["stamp_announcements"]("0.1.19")
    assert _table(path)["early"] == "unreleased"


def test_shipped_announcements_parse_for_stamping(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    script = runpy.run_path(str(root / "scripts/stamp_release.py"))
    from meow.project.announcements import ANNOUNCED

    unreleased = [key for key, (since, _) in ANNOUNCED.items() if since == "unreleased"]
    assert script["unreleased"]() == unreleased


@pytest.mark.parametrize("script", ["stamp_release.py", "smoke_release.py"])
def test_release_tools_import_without_installed_dependencies(script):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            f"import runpy; runpy.run_path('scripts/{script}', "
            "run_name='import_check')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_update_flags_are_standalone():
    parser = _build_arg_parser()
    assert parser.parse_args(["--update"]).update
    assert parser.parse_args(["-u"]).update
    assert parser.parse_args(["--update", "--beta"]).beta
    assert parser.parse_args(["--update", "--latest-pypi"]).latest_pypi
    assert parser.parse_args(["--update", "--version", "0.2.0b1"]).version == "0.2.0b1"
    for argv in (
        ["--beta"],
        ["--latest-pypi"],
        ["--version", "0.2.0"],
        ["--update", "--beta", "--latest-pypi"],
        ["--update", "--beta", "--version", "0.2.0"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
    with pytest.raises(SystemExit):
        parser.parse_args(["--update", "status", "run-1"])


def _index(*names):
    return {
        "files": [
            {
                "filename": name,
                "hashes": {"sha256": str(number) * 64},
                "yanked": False,
            }
            for number, name in enumerate(names)
        ]
    }


def test_latest_release_selects_newest_stable_wheel_from_index(monkeypatch):
    body = _index(
        "meow_harness-0.2.0-py3-none-any.whl",
        "meow_harness-0.2.0.tar.gz",
        "meow_harness-0.3.0b1-py3-none-any.whl",
        "meow_harness-0.3.0rc1-py3-none-any.whl",
    )
    urls = []

    def fetch(request, **kwargs):
        urls.append((request.full_url, request.headers["Accept"]))
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(maintenance, "urlopen", fetch)
    assert maintenance.latest_release() == ("0.2.0", "0" * 64)
    assert maintenance.latest_version() == "0.2.0"
    assert urls[0] == (
        "https://pypi.org/simple/meow-harness/",
        "application/vnd.pypi.simple.v1+json",
    )


def test_latest_release_channels_and_pins(monkeypatch):
    body = _index(
        "meow_harness-0.2.0-py3-none-any.whl",
        "meow_harness-0.3.0b2-py3-none-any.whl",
        "meow_harness-0.3.0b10-py3-none-any.whl",
        "meow_harness-0.3.0rc1-py3-none-any.whl",
    )
    monkeypatch.setattr(
        maintenance, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(body).encode())
    )
    assert maintenance.latest_release(channel="stable")[0] == "0.2.0"
    assert maintenance.latest_release(channel="beta") == ("0.3.0b10", "2" * 64)
    assert maintenance.latest_release(channel="any")[0] == "0.3.0b10"
    assert maintenance.latest_release(pin="0.3.0b2") == ("0.3.0b2", "1" * 64)
    assert maintenance.latest_release(pin="0.2.0")[0] == "0.2.0"
    with pytest.raises(ValueError, match=r"0\.9\.0"):
        maintenance.latest_release(pin="0.9.0")
    with pytest.raises(ValueError):
        maintenance.latest_release(pin="0.2")
    body["files"].append(_index("meow_harness-0.3.0-py3-none-any.whl")["files"][0])
    assert maintenance.latest_release(channel="any")[0] == "0.3.0"
    assert maintenance.latest_release(channel="beta")[0] == "0.3.0b10"


@pytest.mark.parametrize(
    "kwargs", [{"channel": "beta"}, {"channel": "any"}, {"pin": "0.2.0"}]
)
def test_only_the_newest_stable_lookup_uses_legacy_json(monkeypatch, kwargs):
    def fetch(request, **kw):
        raise OSError("HTTP 406")

    monkeypatch.setattr(maintenance, "urlopen", fetch)
    with pytest.raises(OSError):
        maintenance.latest_release(**kwargs)


@pytest.mark.parametrize("primary", ["error", "empty"])
def test_latest_release_falls_back_to_legacy_project_json(monkeypatch, primary):
    legacy = {
        "info": {"version": "0.4.0"},
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "yanked": "withdrawn",
                "digests": {"sha256": "y" * 64},
            },
            {
                "packagetype": "sdist",
                "yanked": False,
                "digests": {"sha256": "s" * 64},
            },
            {
                "packagetype": "bdist_wheel",
                "yanked": False,
                "digests": {"sha256": "b" * 64},
            },
        ],
    }
    responses = [
        OSError("HTTP 406") if primary == "error" else {"files": []},
        legacy,
    ]
    urls = []

    def fetch(request, **kwargs):
        urls.append(request.full_url if hasattr(request, "full_url") else request)
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(maintenance, "urlopen", fetch)
    assert maintenance.latest_release() == ("0.4.0", "b" * 64)
    assert urls == [
        "https://pypi.org/simple/meow-harness/",
        "https://pypi.org/pypi/meow-harness/json",
    ]


def _fake_pythons(monkeypatch, versions):
    from meow.installer import releases

    monkeypatch.delenv("MEOW_PYTHON", raising=False)
    monkeypatch.setattr(releases, "_windows_candidates", lambda: list(versions))
    monkeypatch.setattr(releases, "_path_candidates", lambda: list(versions))
    monkeypatch.setattr(
        releases,
        "_probe",
        lambda path: versions[path],
    )


def test_find_python_picks_the_newest_supported_install(monkeypatch):
    from pathlib import Path

    from meow.installer.releases import find_python

    _fake_pythons(
        monkeypatch,
        {
            "old": ((3, 11, 9), Path("old")),
            "stable": ((3, 12, 4), Path("stable")),
            "latest": ((3, 14, 1), Path("latest")),
            "store": None,
        },
    )
    assert find_python() == Path("latest")


def test_find_python_explains_missing_python(monkeypatch):
    from pathlib import Path

    from meow.installer.releases import find_python

    _fake_pythons(monkeypatch, {"old": ((3, 11, 9), Path("old")), "store": None})
    with pytest.raises(RuntimeError, match=r"3\.12"):
        find_python()


def test_find_python_honors_override(monkeypatch):
    from meow.installer.releases import find_python

    monkeypatch.setenv("MEOW_PYTHON", sys.executable)
    assert find_python().exists()


def _bundle(tmp_path, name):
    bundle = tmp_path / name
    (bundle / "runtime").mkdir(parents=True)
    (bundle / "runtime" / "marker").write_text(name)
    return bundle


def test_swap_replaces_an_existing_version_and_drops_the_old_build(tmp_path):
    from meow.installer.releases import _swap

    destination = tmp_path / "0.2.0"
    destination.mkdir()
    (destination / "old").write_text("old")
    _swap(_bundle(tmp_path, "new"), destination)
    assert (destination / "runtime" / "marker").read_text() == "new"
    assert not (destination / "old").exists()
    assert not list(tmp_path.glob(".old-*"))


def test_swap_restores_the_old_version_when_the_new_one_cannot_move(
    tmp_path, monkeypatch
):
    from meow.installer.releases import _swap

    destination = tmp_path / "0.2.0"
    destination.mkdir()
    (destination / "old").write_text("old")
    bundle = _bundle(tmp_path, "new")
    original = os.replace

    def replace(source, target):
        if Path(source) == bundle:
            raise PermissionError("locked")
        return original(source, target)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr("meow.common.atomic.time.sleep", lambda _: None)
    monkeypatch.setattr("meow.common.atomic.shutil.copytree", _boom)
    with pytest.raises(OSError, match="disk"):
        _swap(bundle, destination)
    assert (destination / "old").read_text() == "old"
    assert not destination.joinpath("runtime").exists()


def _boom(*args, **kwargs):
    raise OSError("disk full")


def test_swap_copies_when_windows_keeps_refusing_the_rename(tmp_path, monkeypatch):
    from meow.installer.releases import _swap

    destination = tmp_path / "0.2.0"
    bundle = _bundle(tmp_path, "new")
    original = os.replace
    attempts = []

    def replace(source, target):
        if Path(source) == bundle:
            attempts.append(1)
            raise PermissionError(5, "Access is denied")
        return original(source, target)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr("meow.common.atomic.time.sleep", lambda _: None)
    _swap(bundle, destination)
    assert len(attempts) > 1
    assert (destination / "runtime" / "marker").read_text() == "new"
    assert not bundle.exists()


def test_swap_retries_a_transient_access_denied(tmp_path, monkeypatch):
    from meow.installer.releases import _swap

    destination = tmp_path / "0.2.0"
    bundle = _bundle(tmp_path, "new")
    original = os.replace
    failures = [PermissionError(5, "Access is denied")] * 3

    def replace(source, target):
        if Path(source) == bundle and failures:
            raise failures.pop()
        return original(source, target)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr("meow.common.atomic.time.sleep", lambda _: None)
    _swap(bundle, destination)
    assert (destination / "runtime" / "marker").read_text() == "new"


def test_swap_reports_a_version_in_use(tmp_path, monkeypatch):
    from meow.installer.releases import _swap

    destination = tmp_path / "0.2.0"
    destination.mkdir()
    original = os.replace

    def replace(source, target):
        if Path(source) == destination:
            raise PermissionError("in use")
        return original(source, target)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr("meow.common.atomic.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="in use"):
        _swap(_bundle(tmp_path, "new"), destination)
    assert destination.is_dir()


def test_stage_bundle_installs_only_the_hashed_wheel(tmp_path, monkeypatch):
    from meow.installer import releases

    calls = []
    monkeypatch.setattr(releases.shutil, "which", lambda name: None)
    monkeypatch.setattr(releases, "_run", lambda *argv, **kw: calls.append(argv) or "")
    bundle = tmp_path / "work" / "bundle"
    bundle.mkdir(parents=True)
    releases.stage_bundle(Path("python"), bundle, "0.2.0", "a" * 64)
    assert calls[0][1:3] == ("-m", "venv")
    requirement = (tmp_path / "work" / "wheel.txt").read_text()
    assert requirement == "meow-harness==0.2.0 --hash=sha256:" + "a" * 64 + "\n"
    pip = calls[1]
    assert {"--no-deps", "--require-hashes", "--only-binary"} <= set(pip)


def test_venv_prefers_uv_and_falls_back_to_venv(tmp_path, monkeypatch):
    from meow.installer import releases

    calls = []
    uv_fails = False

    def run(*argv, **kwargs):
        calls.append(argv)
        if argv[0] == "uv" and uv_fails:
            raise RuntimeError("uv broke")
        return ""

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(releases, "_run", run)
    monkeypatch.setattr(releases.shutil, "which", lambda name: "/bin/uv")
    releases._venv(Path("python"), runtime)
    assert calls == [
        ("uv", "venv", "--quiet", "--seed", "--python", "python", str(runtime))
    ]
    calls.clear()
    uv_fails = True
    releases._venv(Path("python"), runtime)
    assert [call[0] for call in calls] == ["uv", "python"]
    assert calls[1][1:3] == ("-m", "venv")


def test_venv_uses_stdlib_venv_without_uv(tmp_path, monkeypatch):
    from meow.installer import releases

    calls = []
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(releases, "_run", lambda *argv, **kw: calls.append(argv) or "")
    monkeypatch.setattr(releases.shutil, "which", lambda name: None)
    releases._venv(Path("python"), runtime)
    assert calls == [("python", "-m", "venv", str(runtime))]


def test_pip_uses_uv_only_when_installed(monkeypatch):
    from meow.installer import releases

    calls = []
    monkeypatch.setattr(releases, "_run", lambda *argv, **kw: calls.append(argv) or "")
    monkeypatch.setattr(releases.shutil, "which", lambda name: "/bin/uv")
    releases._pip(Path("python"), "-r", "lock")
    assert calls == [
        ("uv", "pip", "install", "--quiet", "--python", "python", "-r", "lock")
    ]
    calls.clear()
    monkeypatch.setattr(releases.shutil, "which", lambda name: None)
    releases._pip(Path("python"), "-r", "lock")
    assert calls[0][1:4] == ("-m", "pip", "install")


def test_pip_retries_with_pip_when_uv_fails(monkeypatch, capsys):
    # The fallback is silent: pip is the baseline, uv only an optimisation.
    from meow.installer import releases

    calls = []

    def run(*argv, **kwargs):
        calls.append(argv)
        if argv[0] == "uv":
            raise RuntimeError("no matching index")
        return ""

    monkeypatch.setattr(releases, "_run", run)
    monkeypatch.setattr(releases.shutil, "which", lambda name: "/bin/uv")
    releases._pip(Path("python"), "-r", "lock")
    assert [call[0] for call in calls] == ["uv", "python"]
    assert capsys.readouterr().out == ""


def test_activation_switches_launcher_after_plugin_registration(tmp_path, monkeypatch):
    from meow.installer.releases import activate

    root = tmp_path / ".meow"
    release = root / "versions" / "0.2.0"
    (release / "bin").mkdir(parents=True)
    executable = "meow.exe" if os.name == "nt" else "meow"
    (release / "bin" / executable).write_bytes(b"launcher")
    (release / "plugin" / ".claude-plugin").mkdir(parents=True)
    (release / "plugin" / ".claude-plugin" / "marketplace.json").write_text(
        '{"name":"meow"}'
    )
    (root / "current-version").write_text("0.1.3")
    settings = tmp_path / "settings.json"
    monkeypatch.setattr("meow.installer.releases.settings_path", lambda: settings)
    monkeypatch.setattr("meow.installer.releases._add_to_path", lambda path: None)
    activate(release)
    assert (root / "current-version").read_text().strip() == "0.2.0"
    assert (root / "bin" / executable).read_bytes() == b"launcher"
    data = json.loads(settings.read_text())
    assert data["extraKnownMarketplaces"]["meow"]["source"]["path"] == str(
        release / "plugin"
    )


def test_failed_activation_keeps_previous_release(tmp_path, monkeypatch):
    from meow.installer.releases import activate

    root = tmp_path / ".meow"
    release = root / "versions" / "0.2.0"
    release.mkdir(parents=True)
    (root / "current-version").write_text("0.1.3")
    settings = tmp_path / "settings.json"
    settings.write_text("malformed")
    monkeypatch.setattr("meow.installer.releases.settings_path", lambda: settings)
    with pytest.raises((ValueError, FileNotFoundError)):
        activate(release)
    assert (root / "current-version").read_text() == "0.1.3"


def test_finish_bundle_reports_each_slow_step(tmp_path, monkeypatch, capsys):
    from meow.installer import releases

    steps = []
    monkeypatch.setattr(releases, "release_python", lambda bundle: bundle / "py")
    monkeypatch.setattr(releases, "_pip", lambda *args: steps.append("pip"))
    monkeypatch.setattr(releases, "_build_launcher", lambda b: steps.append("launcher"))
    monkeypatch.setattr(releases.shutil, "copytree", lambda *a: steps.append("plugin"))
    monkeypatch.setattr(releases.shutil, "copy2", lambda *a: None)
    monkeypatch.setattr(releases, "_swap", lambda *a: steps.append("swap"))

    releases.finish_bundle(tmp_path / "bundle", tmp_path / "dest")

    printed = capsys.readouterr().out.lower()
    assert steps == ["pip", "launcher", "plugin", "swap"]
    assert "dependencies" in printed and "launcher" in printed

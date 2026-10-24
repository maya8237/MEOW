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


@pytest.mark.parametrize("tag", ["1.2.3", "2.0.0"])
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
    with pytest.raises(SystemExit):
        parser.parse_args(["--update", "status", "run-1"])


def test_latest_version_comes_from_published_release(monkeypatch):
    urls = []

    def fetch(url, **kwargs):
        urls.append(url)
        return io.BytesIO(json.dumps({"tag_name": "0.2.0"}).encode())

    monkeypatch.setattr(maintenance, "urlopen", fetch)
    assert maintenance.latest_version() == "0.2.0"
    assert urls == [
        "https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json"
    ]


def test_latest_release_requires_a_wheel_checksum(monkeypatch):
    def serve(body):
        monkeypatch.setattr(
            maintenance,
            "urlopen",
            lambda url, **kw: io.BytesIO(json.dumps(body).encode()),
        )

    serve({"tag_name": "0.2.0", "wheel_sha256": "a" * 64})
    assert maintenance.latest_release() == ("0.2.0", "a" * 64)
    serve({"tag_name": "0.2.0"})
    with pytest.raises(ValueError, match="checksum"):
        maintenance.latest_release()


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
    original = Path.rename

    def rename(self, target):
        if self == bundle:
            raise PermissionError("locked")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(PermissionError):
        _swap(bundle, destination)
    assert (destination / "old").read_text() == "old"


def test_swap_reports_a_version_in_use(tmp_path, monkeypatch):
    from meow.installer.releases import _swap

    destination = tmp_path / "0.2.0"
    destination.mkdir()
    original = Path.rename

    def rename(self, target):
        if self == destination:
            raise PermissionError("in use")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(RuntimeError, match="in use"):
        _swap(_bundle(tmp_path, "new"), destination)
    assert destination.is_dir()


def test_stage_bundle_installs_only_the_hashed_wheel(tmp_path, monkeypatch):
    from meow.installer import releases

    calls = []
    monkeypatch.setattr(releases, "_run", lambda *argv, **kw: calls.append(argv) or "")
    bundle = tmp_path / "work" / "bundle"
    bundle.mkdir(parents=True)
    releases.stage_bundle(Path("python"), bundle, "0.2.0", "a" * 64)
    assert calls[0][1:3] == ("-m", "venv")
    requirement = (tmp_path / "work" / "wheel.txt").read_text()
    assert requirement == "meow-harness==0.2.0 --hash=sha256:" + "a" * 64 + "\n"
    pip = calls[1]
    assert {"--no-deps", "--require-hashes", "--only-binary"} <= set(pip)


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

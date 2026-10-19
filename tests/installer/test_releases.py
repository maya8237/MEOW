import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from meow.cli.parser import _build_arg_parser
from meow.installer import maintenance


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2.3"])
def test_release_tag_sets_package_and_plugin_versions(tmp_path, monkeypatch, tag):
    root = Path(__file__).resolve().parents[2]
    script = runpy.run_path(str(root / "scripts/build_release.py"))
    monkeypatch.setitem(script["check_version"].__globals__, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_REF_NAME", tag)
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "meow"\nversion = "0.0.1"\n')
    manifests = tmp_path / ".claude-plugin"
    manifests.mkdir()
    (manifests / "plugin.json").write_text('{"name":"meow","version":"0.0.1"}')
    (manifests / "marketplace.json").write_text(
        '{"plugins":[{"name":"meow","version":"0.0.2"}]}'
    )

    assert script["check_version"]() == "1.2.3"
    assert 'version = "1.2.3"' in project.read_text()
    assert json.loads((manifests / "plugin.json").read_text())["version"] == "1.2.3"
    assert (
        json.loads((manifests / "marketplace.json").read_text())["plugins"][0][
            "version"
        ]
        == "1.2.3"
    )


@pytest.mark.parametrize("script", ["build_release.py", "smoke_release.py"])
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


def test_update_flags_and_auto_do_not_conflict():
    parser = _build_arg_parser()
    assert parser.parse_args(["--update"]).update
    assert parser.parse_args(["-u"]).update
    assert parser.parse_args(["run", "hello", "-a"]).auto
    assert parser.parse_args(["run", "hello", "--auto"]).auto
    with pytest.raises(SystemExit):
        parser.parse_args(["--update", "status", "run-1"])


def test_latest_version_comes_from_published_release(monkeypatch):
    urls = []

    def fetch(url, **kwargs):
        urls.append(url)
        return io.BytesIO(json.dumps({"tag_name": "v0.2.0"}).encode())

    monkeypatch.setattr(maintenance, "urlopen", fetch)
    assert maintenance.latest_version() == "0.2.0"
    assert urls == [
        "https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json"
    ]


def test_install_archive_checks_digest_and_preserves_config(tmp_path):
    from meow.installer.releases import unpack_release

    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("runtime/python.exe", b"python")
        bundle.writestr("plugin/skills/run/SKILL.md", b"skill")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    destination = tmp_path / ".meow" / "versions" / "v0.2.0"
    config = tmp_path / ".meow" / "config.toml"
    config.parent.mkdir()
    config.write_text("keep me")
    with pytest.raises(ValueError, match="checksum"):
        unpack_release(archive, "0" * 64, destination)
    assert not destination.exists()
    unpack_release(archive, digest, destination)
    assert (destination / "runtime/python.exe").read_bytes() == b"python"
    assert config.read_text() == "keep me"


def test_install_rejects_archive_path_traversal(tmp_path):
    from meow.installer.releases import unpack_release

    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside", b"bad")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="archive"):
        unpack_release(archive, digest, tmp_path / "version")
    assert not (tmp_path / "outside").exists()


def test_activation_switches_launcher_after_plugin_registration(tmp_path, monkeypatch):
    from meow.installer.releases import activate

    root = tmp_path / ".meow"
    release = root / "versions" / "v0.2.0"
    (release / "bin").mkdir(parents=True)
    executable = "meow.exe" if os.name == "nt" else "meow"
    (release / "bin" / executable).write_bytes(b"launcher")
    (release / "plugin" / ".claude-plugin").mkdir(parents=True)
    (release / "plugin" / ".claude-plugin" / "marketplace.json").write_text(
        '{"name":"meow"}'
    )
    (root / "current-version").write_text("v0.1.3")
    settings = tmp_path / "settings.json"
    monkeypatch.setattr("meow.installer.releases.settings_path", lambda: settings)
    monkeypatch.setattr("meow.installer.releases._add_to_path", lambda path: None)
    activate(release)
    assert (root / "current-version").read_text().strip() == "v0.2.0"
    assert (root / "bin" / executable).read_bytes() == b"launcher"
    data = json.loads(settings.read_text())
    assert data["extraKnownMarketplaces"]["meow"]["source"]["path"] == str(
        release / "plugin"
    )


def test_failed_activation_keeps_previous_release(tmp_path, monkeypatch):
    from meow.installer.releases import activate

    root = tmp_path / ".meow"
    release = root / "versions" / "v0.2.0"
    release.mkdir(parents=True)
    (root / "current-version").write_text("v0.1.3")
    settings = tmp_path / "settings.json"
    settings.write_text("malformed")
    monkeypatch.setattr("meow.installer.releases.settings_path", lambda: settings)
    with pytest.raises((ValueError, FileNotFoundError)):
        activate(release)
    assert (root / "current-version").read_text() == "v0.1.3"

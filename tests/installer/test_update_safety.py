"""`meow -u` must not rewrite user shell profiles or settings choices."""

import json

import pytest

from meow.installer import releases
from meow.project.claude_settings import register_checkout_plugin


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/bash")
    return tmp_path


def test_path_untouched_when_bin_already_on_it(home, monkeypatch):
    bin_dir = home / ".meow" / "bin"
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin")

    releases._add_to_path(bin_dir)

    assert not (home / ".profile").exists()
    assert not (home / ".bashrc").exists()


def test_first_install_still_adds_profiles(home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    if releases.os.name == "nt":
        pytest.skip("POSIX shell profiles")

    releases._add_to_path(home / ".meow" / "bin")

    assert ".meow/bin" in (home / ".bashrc").read_text()


def plugin_checkout(path):
    (path / ".claude-plugin").mkdir(parents=True)
    (path / ".claude-plugin" / "marketplace.json").write_text('{"name": "meow"}')
    return path


def test_update_keeps_a_plugin_the_user_disabled(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {"meow@meow": False}}))

    register_checkout_plugin(settings, plugin_checkout(tmp_path / "v2"))

    data = json.loads(settings.read_text())
    assert data["enabledPlugins"]["meow@meow"] is False
    assert data["extraKnownMarketplaces"]["meow"]["source"]["path"].endswith("v2")


def test_invalid_settings_do_not_fail_an_update(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    monkeypatch.setattr(releases, "settings_path", lambda: settings)

    releases._register_plugin(plugin_checkout(tmp_path / "v2"))

    assert "not registered" in capsys.readouterr().err
    assert settings.read_text() == "{not json"

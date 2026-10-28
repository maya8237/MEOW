"""Manifest fallbacks refresh without becoming explicit configuration."""

import json

import pytest

from meow.project.config import load_config


@pytest.fixture(autouse=True)
def no_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "user"
    )


@pytest.mark.parametrize(
    "manager,lock",
    [
        ("npm", "package-lock.json"),
        ("pnpm", "pnpm-lock.yaml"),
        ("yarn", "yarn.lock"),
        ("bun", "bun.lock"),
    ],
)
def test_script_checks_are_runtime_defaults(tmp_path, manager, lock):
    (tmp_path / "package.json").write_text(
        json.dumps({
            "scripts": {
                "lint": "eslint .",
                "test": "node --test",
                "build": "tsc",
            }
        })
    )
    (tmp_path / lock).touch()
    config = load_config(tmp_path)
    assert config["tester"]["tests"][0].command == manager
    assert config["tester"]["tests"][0].args == ("run", "test")
    assert config["tester"]["auto_test"] is False
    assert config["lint"][0].per_file is False
    assert config["build"][0].command == manager
    (tmp_path / "package.json").write_text("{}")
    assert load_config(tmp_path)["tester"]["tests"] == []
    assert not (tmp_path / ".meow").exists()


@pytest.mark.parametrize("scope", ["user", "config.toml", "config.local.toml"])
def test_explicit_empty_checks_survive_detection(tmp_path, scope):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    path = tmp_path / "user" if scope == "user" else tmp_path / ".meow" / scope
    path.parent.mkdir(exist_ok=True)
    path.write_text("lint = []\nbuild = []\n[tester]\ntests = []\n")
    config = load_config(tmp_path)
    assert config["lint"] == []
    assert config["tester"]["tests"] == []
    assert config["build"] == []


def test_python_checks_and_legacy_tester_opt_in(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    config = load_config(tmp_path)
    assert config["tester"]["tests"][0].args == ("-m", "pytest")
    assert config["tester"]["auto_test"] is False
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text(
        '[[tester.tests]]\ncommand = "pytest"\n'
    )
    assert load_config(tmp_path)["tester"]["auto_test"] is True


def test_ambiguous_managers_and_malformed_manifests(tmp_path):
    from meow.project.command_detection import detect_commands

    (tmp_path / "package.json").write_text('{"scripts":{"test":"secret"}}')
    (tmp_path / "yarn.lock").touch()
    (tmp_path / "bun.lock").touch()
    detected = detect_commands(tmp_path)
    assert detected.defaults == {}
    assert detected.ambiguous == ("package.json: package manager",)
    (tmp_path / "package.json").write_text("[]")
    (tmp_path / "pyproject.toml").write_text("broken = [")
    assert detect_commands(tmp_path).defaults == {}


def test_declared_manager_overrides_stale_lock_and_mixed_checks(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({
            "packageManager": "pnpm@10.0.0",
            "scripts": {"test": "secret-token", "lint": "eslint ."},
        })
    )
    (tmp_path / "package-lock.json").touch()
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    config = load_config(tmp_path)
    assert [entry.command for entry in config["tester"]["tests"]] == ["pnpm", "python"]
    assert [entry.command for entry in config["lint"]] == [
        "pnpm",
        "python -m ruff check",
    ]
    assert config["tester"]["auto_test"] is False


def test_bounded_detection_ignores_nested_and_oversized_manifests(
    tmp_path, monkeypatch
):
    from meow.project.command_detection import detect_commands

    monkeypatch.setattr(
        "subprocess.run", lambda *a, **kw: pytest.fail("executed command")
    )
    nested = tmp_path / "node_modules/pkg"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "package.json").write_text(" " * (256 * 1024 + 1))
    assert detect_commands(tmp_path).defaults == {}


def test_receipt_refresh_and_status_are_safe(tmp_path, monkeypatch, capsys):  # ruff: ignore[too-many-statements]
    import subprocess

    from meow.project.onboarding import feature_gaps, onboard_if_needed

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr("meow.project.repos.remember", lambda *a: None)
    package = tmp_path / "package.json"
    package.write_text('{"scripts":{"test":"TOKEN=secret node --test"}}')
    first = onboard_if_needed(tmp_path, tmp_path)
    assert first["detected"] == {"tester.tests": "package.json:scripts.test"}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tester.tests" in captured.err and "secret" not in captured.err
    receipt = tmp_path / ".meow/setup-receipt.json"
    assert "secret" not in receipt.read_text()
    config = tmp_path / ".meow/config.toml"
    before = config.read_bytes()
    timestamp = receipt.stat().st_mtime_ns
    assert onboard_if_needed(tmp_path, tmp_path) is None
    assert receipt.stat().st_mtime_ns == timestamp
    assert capsys.readouterr().err == ""
    status = feature_gaps(tmp_path)["tester_tests"]
    assert status["configured"] and status["detected"] and not status["verified"]
    package.write_text('{"scripts":{"build":"tsc"}}')
    refreshed = onboard_if_needed(tmp_path, tmp_path)
    assert refreshed["detected"] == {"build": "package.json:scripts.build"}
    assert config.read_bytes() == before
    assert load_config(tmp_path)["tester"]["tests"] == []


def test_registry_binding_is_persisted_once(tmp_path, monkeypatch):
    import subprocess

    from meow.project.onboarding import feature_gaps, onboard_if_needed

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    registry = {"references": {"jira": {"from": "company-jira"}}, "hints": {}}
    monkeypatch.setattr(
        "meow.project.mcp_registry.discover_provider_references", lambda root: registry
    )
    onboard_if_needed(tmp_path, tmp_path)
    local = tmp_path / ".meow/config.local.toml"
    before = local.read_bytes()
    assert load_config(tmp_path)["jira"]["mcp"] == {"from": "company-jira"}
    registry["references"]["jira"] = {"from": "different-jira"}
    assert onboard_if_needed(tmp_path, tmp_path) is None
    assert local.read_bytes() == before
    assert load_config(tmp_path)["jira"]["mcp"]["from"] == "company-jira"
    status = feature_gaps(tmp_path)["jira"]
    assert status["detected"] and not status["verified"]


@pytest.mark.parametrize("scope", ["user", "config.toml", "config.local.toml"])
def test_registry_never_overrides_explicit_mcp(tmp_path, monkeypatch, scope):
    import subprocess

    from meow.project.onboarding import onboard_project

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "user" if scope == "user" else tmp_path / ".meow" / scope
    path.parent.mkdir(exist_ok=True)
    path.write_text("[jira]\nmcp = {}\n")
    monkeypatch.setattr(
        "meow.project.mcp_registry.discover_provider_references",
        lambda root: {
            "references": {"jira": {"from": "detected"}},
            "hints": {},
        },
    )
    assert onboard_project(tmp_path).error is None
    assert load_config(tmp_path)["jira"]["mcp"] == {}


@pytest.mark.parametrize("auto_test", [True, False, "invalid"])
def test_explicit_ai_tester_opt_in_is_validated(tmp_path, auto_test):
    from meow.project.config_schema import _normalize_tester_config

    raw = {"tester": {"auto_test": auto_test}}
    if auto_test == "invalid":
        with pytest.raises(ValueError, match=r"auto_test.*boolean"):
            _normalize_tester_config(raw)
    else:
        assert _normalize_tester_config(raw)["auto_test"] is auto_test


@pytest.mark.parametrize(
    "manifest,content",
    [
        ("package.json", '{"devDependencies":{"eslint":"^9"}}'),
        ("eslint.config.mjs", "export default []"),
    ],
)
def test_eslint_fallback_without_script(tmp_path, manifest, content):
    (tmp_path / manifest).write_text(content)
    assert load_config(tmp_path)["lint"][0].command == "npx --no-install eslint"


def test_provider_ambiguity_refreshes_receipt_and_status(tmp_path, monkeypatch, capsys):
    import subprocess

    from meow.project.onboarding import feature_gaps, onboard_if_needed

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    registry = {"references": {}, "hints": {}}
    monkeypatch.setattr(
        "meow.project.mcp_registry.discover_provider_references", lambda root: registry
    )
    onboard_if_needed(tmp_path, tmp_path)
    registry["hints"]["jira"] = "Multiple registrations match; configure a reference."
    report = onboard_if_needed(tmp_path, tmp_path)
    assert report["ambiguous"] == [
        "jira: Multiple registrations match; configure a reference."
    ]
    cached = json.loads((tmp_path / ".meow/setup-receipt.json").read_text())
    assert cached["ambiguous"] == report["ambiguous"]
    assert feature_gaps(tmp_path)["jira"]["ambiguous"] == report["ambiguous"]
    assert onboard_if_needed(tmp_path, tmp_path) is None
    assert capsys.readouterr().out == ""


def test_bound_providers_do_not_warn_about_registry_ambiguity(tmp_path, monkeypatch):
    from meow.project.onboarding import feature_gaps

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.local.toml").write_text('[jira.mcp]\nfrom = "stable"\n')
    monkeypatch.setattr(
        "meow.project.mcp_registry.discover_provider_references",
        lambda root: {
            "references": {},
            "hints": {"jira": "Multiple registrations match."},
        },
    )
    assert feature_gaps(tmp_path)["jira"]["ambiguous"] == []

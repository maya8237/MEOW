import json
import subprocess

import pytest

from meow.project.onboarding import onboard_project
from tests.native.test_native_cli import run_native


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "none"
    )
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_onboard_status_reports_not_onboarded(repo):
    code, out, _ = run_native("onboard-status", "--working-dir", str(repo))
    assert code == 0
    data = json.loads(out)
    assert set(data) == {"onboarded", "config_exists", "boundary_ok", "gaps"}
    assert data["onboarded"] is False
    assert data["gaps"]["jira"]["configured"] is False


def test_onboard_status_reports_onboarded(repo):
    onboard_project(repo)
    code, out, _ = run_native("onboard-status", "--working-dir", str(repo))
    data = json.loads(out)
    assert code == 0
    assert data["onboarded"] is True
    assert data["config_exists"] is True
    assert data["boundary_ok"] is True

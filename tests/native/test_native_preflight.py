import json
from unittest.mock import patch

from meow.infrastructure import tester_preflight as preflight
from meow.native import native
from tests.native.test_native_cli import run_native


def fake_checks(status):
    async def run(active_dir, config):  # ruff: ignore[unused-async] -- awaited by verify
        return [preflight.PreflightCheck("target", status, "d", "f", "s")]

    return run


def test_verify_has_no_preflight_keys_without_the_flag(tmp_path):
    with patch.object(preflight, "run_preflight", fake_checks("fail")) as run:
        result = native.verify(tmp_path, run_lint=False)
    assert "preflight" not in result
    assert "preflight_ok" not in result
    assert run is not None


def test_verify_adds_preflight_results_with_the_flag(tmp_path):
    with patch.object(preflight, "run_preflight", fake_checks("ok")):
        result = native.verify(tmp_path, run_lint=False, preflight=True)
    assert result["preflight_ok"] is True
    assert result["preflight"][0] == {
        "name": "target",
        "status": "ok",
        "detail": "d",
        "fix": "f",
        "snippet": "s",
    }
    with patch.object(preflight, "run_preflight", fake_checks("fail")):
        assert (
            native.verify(tmp_path, run_lint=False, preflight=True)["preflight_ok"]
            is False
        )


def test_skipped_checks_do_not_make_the_preflight_fail(tmp_path):
    with patch.object(preflight, "run_preflight", fake_checks("skipped")):
        result = native.verify(tmp_path, run_lint=False, preflight=True)
    assert result["preflight_ok"] is True


def test_cli_flag_reaches_verify_and_prints_json(tmp_path):
    with patch.object(preflight, "run_preflight", fake_checks("ok")):
        code, out, _ = run_native(
            "verify", "--work-dir", str(tmp_path), "--no-lint", "--preflight"
        )
    assert code == 0
    assert json.loads(out)["preflight_ok"] is True

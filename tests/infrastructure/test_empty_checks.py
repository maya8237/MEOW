import asyncio

import pytest

from meow.infrastructure.checks import Check, checks_current, run_final_checks


def test_default_project_without_checks_needs_no_config_file(tmp_path):
    config = {"lint": [], "tester": {"tests": []}, "build": []}
    assert asyncio.run(run_final_checks(tmp_path, config)) == []


def test_empty_evidence_is_current_when_no_checks_are_required(tmp_path):
    assert checks_current([], [], tmp_path)


def test_nonempty_checks_still_require_configuration_evidence(tmp_path):
    with pytest.raises(FileNotFoundError):
        checks_current([], [Check("build", "build-1", "missing")], tmp_path)

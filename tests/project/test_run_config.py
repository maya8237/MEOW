"""`[run]` retry-ladder config defaults and validation."""

import pytest

from meow.execution.run_failures import InvalidConfigError
from meow.project.config import load_config, run_config
from meow.project.config_schema import RUN_DEFAULTS


def write_config(repo, text):
    (repo / ".meow").mkdir(exist_ok=True)
    (repo / ".meow" / "config.toml").write_text(text, encoding="utf-8")


def test_run_defaults_filled(tmp_path):
    assert RUN_DEFAULTS == {
        "auto_retries": 3,
        "retry_backoff": [60, 300, 900],
        "managed_resumes": 6,
        "max_total_resumes": 20,
        "api_max_wait": 10800,
        "worker_startup_grace": 180,
        "supervise": False,
    }
    assert load_config(tmp_path)["run"] == RUN_DEFAULTS
    assert run_config(tmp_path) == RUN_DEFAULTS


def test_run_overrides_merge_with_defaults(tmp_path):
    write_config(tmp_path, "[run]\nauto_retries = 0\nretry_backoff = [1, 2.5]\n")
    expected = {**RUN_DEFAULTS, "auto_retries": 0, "retry_backoff": [1, 2.5]}
    assert load_config(tmp_path)["run"] == expected
    assert run_config(tmp_path) == expected


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("auto_retries = -1", "auto_retries"),
        ("auto_retries = true", "auto_retries"),
        ("retry_backoff = []", "retry_backoff"),
        ("retry_backoff = [60, 'x']", "retry_backoff"),
        ("managed_resumes = 1.5", "managed_resumes"),
        ("api_max_wait = 0", "api_max_wait"),
        ("worker_startup_grace = -3", "worker_startup_grace"),
        ("supervise = 1", "supervise"),
        ("nope = 1", "unknown keys"),
    ],
)
def test_run_rejects_bad_values(tmp_path, line, key):
    write_config(tmp_path, f"[run]\n{line}\n")
    with pytest.raises(InvalidConfigError, match=rf"\[run\].*{key}|{key}"):
        load_config(tmp_path)
    with pytest.raises(ValueError, match="run"):
        run_config(tmp_path)


def test_invalid_config_is_a_value_error(tmp_path):
    write_config(tmp_path, "max_rounds = 0\n")
    with pytest.raises(ValueError, match="max_rounds") as raised:
        load_config(tmp_path)
    assert raised.value.cause == "invalid_config"

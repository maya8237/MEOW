"""`[pipeline]` config defaults and validation."""

import pytest

from meow.project.config import load_config
from meow.project.config_schema import PIPELINE_DEFAULTS


def write_config(repo, text):
    (repo / ".meow").mkdir(exist_ok=True)
    (repo / ".meow" / "config.toml").write_text(text, encoding="utf-8")


def test_pipeline_defaults_filled(tmp_path):
    assert PIPELINE_DEFAULTS == {
        "open_mr": True,
        "max_fix_rounds": 3,
        "start_timeout": 300,
        "timeout": 3600,
        "poll_seconds": 15,
        "fix_non_meow_jobs": False,
    }
    assert load_config(tmp_path)["pipeline"] == PIPELINE_DEFAULTS


def test_pipeline_overrides_merge_with_defaults(tmp_path):
    write_config(tmp_path, "[pipeline]\nmax_fix_rounds = 0\nopen_mr = false\n")
    pipeline = load_config(tmp_path)["pipeline"]
    assert pipeline == {**PIPELINE_DEFAULTS, "max_fix_rounds": 0, "open_mr": False}


def test_pipeline_rejects_unknown_key(tmp_path):
    write_config(tmp_path, "[pipeline]\nbogus = 1\n")
    with pytest.raises(ValueError, match=r"\[pipeline\] has unknown keys: bogus"):
        load_config(tmp_path)


def test_pipeline_rejects_non_table(tmp_path):
    write_config(tmp_path, "pipeline = 3\n")
    with pytest.raises(ValueError, match=r"\[pipeline\] must be a table"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("max_fix_rounds = -1", "max_fix_rounds"),
        ("max_fix_rounds = true", "max_fix_rounds"),
        ("max_fix_rounds = 1.5", "max_fix_rounds"),
        ("poll_seconds = 0", "poll_seconds"),
        ("poll_seconds = true", "poll_seconds"),
        ("start_timeout = 0", "start_timeout"),
        ("timeout = true", "timeout"),
        ('open_mr = "yes"', "open_mr"),
        ("fix_non_meow_jobs = 1", "fix_non_meow_jobs"),
    ],
)
def test_pipeline_rejects_bad_values(tmp_path, line, key):
    write_config(tmp_path, f"[pipeline]\n{line}\n")
    with pytest.raises(ValueError, match=rf"\[pipeline\]\.{key} must be"):
        load_config(tmp_path)

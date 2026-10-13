"""`[orchestrator]` config defaults and validation."""

import pytest

from meow.project.config import load_config

DEFAULTS = {
    "max_parallel": 3,
    "max_recovery_cycles": 3,
    "review_rounds": 3,
    "branch_prefix": "orchestrator/",
    "poll_seconds": 5,
    "open_pr": True,
}


def write_config(repo, text, name="config.toml"):
    (repo / ".meow").mkdir(exist_ok=True)
    (repo / ".meow" / name).write_text(text, encoding="utf-8")


def test_defaults(tmp_path):
    assert load_config(tmp_path)["orchestrator"] == DEFAULTS


def test_values_override_defaults(tmp_path):
    write_config(
        tmp_path,
        "[orchestrator]\n"
        "max_parallel = 5\n"
        "max_recovery_cycles = 0\n"
        "review_rounds = 2\n"
        'branch_prefix = "team/"\n'
        "poll_seconds = 0.5\n"
        "open_pr = false\n",
    )
    assert load_config(tmp_path)["orchestrator"] == {
        "max_parallel": 5,
        "max_recovery_cycles": 0,
        "review_rounds": 2,
        "branch_prefix": "team/",
        "poll_seconds": 0.5,
        "open_pr": False,
    }


def test_local_config_layers_over_shared(tmp_path):
    write_config(tmp_path, "[orchestrator]\nmax_parallel = 2\nopen_pr = false\n")
    write_config(tmp_path, "[orchestrator]\nmax_parallel = 4\n", "config.local.toml")
    orchestrator = load_config(tmp_path)["orchestrator"]
    local_value = 4
    assert orchestrator["max_parallel"] == local_value
    assert orchestrator["open_pr"] is False


def test_rejects_unknown_key(tmp_path):
    write_config(tmp_path, "[orchestrator]\nbudget = 5\nspeed = 1\n")
    with pytest.raises(ValueError, match=r"unknown keys: budget, speed"):
        load_config(tmp_path)


def test_rejects_non_table(tmp_path):
    write_config(tmp_path, 'orchestrator = "yes"\n')
    with pytest.raises(ValueError, match=r"\[orchestrator\] must be a table"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("max_parallel = 0", "max_parallel must be an integer >= 1"),
        ("max_parallel = true", "max_parallel must be an integer >= 1"),
        ('max_parallel = "3"', "max_parallel must be an integer >= 1"),
        ("max_recovery_cycles = -1", "max_recovery_cycles must be an integer >= 0"),
        ("review_rounds = 0", "review_rounds must be an integer >= 1"),
        ("poll_seconds = 0", "poll_seconds must be a number > 0"),
        ("poll_seconds = true", "poll_seconds must be a number > 0"),
        ('open_pr = "yes"', "open_pr must be true or false"),
        ('branch_prefix = ""', "branch_prefix must be a non-empty string"),
        ('branch_prefix = "a b/"', "branch_prefix must be a non-empty string"),
        ('branch_prefix = "a..b/"', "branch_prefix must be a non-empty string"),
        ('branch_prefix = "-x/"', "branch_prefix must be a non-empty string"),
        ("branch_prefix = 7", "branch_prefix must be a non-empty string"),
    ],
)
def test_rejects_bad_values(tmp_path, line, message):
    write_config(tmp_path, f"[orchestrator]\n{line}\n")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)

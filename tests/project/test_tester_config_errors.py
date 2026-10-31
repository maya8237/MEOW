"""Rejected browser settings explain the fix with a paste-ready snippet."""

import pytest

from meow.project.config import load_config


def _load(tmp_path, body):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        f"[tester]\nbase_url = 'http://127.0.0.1:3000'\n{body}\n", encoding="utf-8"
    )
    return load_config(tmp_path)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('browser_channel = "firefox"', 'browser_channel = "chrome"'),
        ('browser_executable = "chrome.exe"', "browser_executable = "),
    ],
)
def test_invalid_browser_setting_includes_a_snippet(tmp_path, body, expected):
    with pytest.raises(ValueError, match=r"for example|absolute path") as caught:
        _load(tmp_path, body)
    assert "[tester]" in str(caught.value)
    assert expected in str(caught.value)

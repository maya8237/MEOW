import pytest

from meow.project.config_schema import BROWSER_CHANNELS, _normalize_tester_config

CHANNELS = (
    "chrome",
    "chrome-beta",
    "chrome-dev",
    "chrome-canary",
    "msedge",
    "msedge-beta",
    "msedge-dev",
)


def normalized(**values):
    return _normalize_tester_config({"tester": values})


@pytest.mark.parametrize("channel", CHANNELS)
def test_every_allowed_channel_loads(channel):
    assert normalized(browser_channel=channel)["browser_channel"] == channel
    assert set(CHANNELS) == set(BROWSER_CHANNELS)


def test_unknown_channel_names_the_key():
    with pytest.raises(ValueError, match="browser_channel"):
        normalized(browser_channel="firefox")


def test_defaults_when_unset():
    config = normalized()
    assert config["browser_channel"] is None
    assert config["browser_executable"] is None
    assert config["browser_playwright"] is False
    assert config["browser_env"] == {}


def test_absolute_executable_loads_and_relative_is_rejected(tmp_path):
    path = str(tmp_path / "chrome")
    assert normalized(browser_executable=path)["browser_executable"] == path
    with pytest.raises(ValueError, match="browser_executable"):
        normalized(browser_executable="chrome/chrome")
    with pytest.raises(ValueError, match="browser_executable"):
        normalized(browser_executable="")


@pytest.mark.parametrize(
    "values",
    [
        {"browser_channel": "chrome", "browser_executable": "/usr/bin/chrome"},
        {"browser_channel": "chrome", "browser_playwright": True},
        {"browser_executable": "/usr/bin/chrome", "browser_playwright": True},
    ],
)
def test_two_selections_are_rejected_naming_both_keys(values):
    with pytest.raises(ValueError) as caught:
        normalized(**values)
    for key in values:
        assert key in str(caught.value)


def test_browser_playwright_must_be_boolean_and_false_is_unselected():
    with pytest.raises(ValueError, match="browser_playwright"):
        normalized(browser_playwright="yes")
    assert (
        normalized(browser_playwright=False, browser_channel="chrome")[
            "browser_channel"
        ]
        == "chrome"
    )


def test_browser_env_accepts_playwright_keys_only():
    env = {"PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS": "true"}
    assert normalized(browser_env=env)["browser_env"] == env
    with pytest.raises(ValueError, match="NODE_OPTIONS"):
        normalized(browser_env={"NODE_OPTIONS": "--inspect"})
    with pytest.raises(ValueError, match="browser_env"):
        normalized(browser_env={"PLAYWRIGHT_X": 1})


def test_base_url_only_config_still_loads_without_a_selection():
    config = normalized(base_url="http://127.0.0.1:3000")
    assert config["base_url"] == "http://127.0.0.1:3000"
    assert config["browser_channel"] is None


def server(url):
    return {"dev_server": [{"command": "bun", "args": ["dev"], "ready_url": url}]}


def test_ready_url_placeholder_only_as_the_port():
    loaded = normalized(**server("http://127.0.0.1:{port}/"))
    assert loaded["dev_server"][0].ready_url == "http://127.0.0.1:{port}/"
    with pytest.raises(ValueError, match="ready_url"):
        normalized(**server("http://{port}.local/"))
    with pytest.raises(ValueError, match="ready_url"):
        normalized(**server("http://127.0.0.1:3000/{port}"))
    assert normalized(**server("http://127.0.0.1:3000/"))["dev_server"]

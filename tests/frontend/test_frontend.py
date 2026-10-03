import sys
from pathlib import Path

from meow.config import DevServerCommand
from meow.frontend import browser_capability


def test_browser_capability_requires_start_and_provider():
    config = {"tester": {"dev_server": [], "browser": None}}
    result = browser_capability(config)
    assert not result.configured
    assert result.reason == "browser provider is not configured"


def test_browser_capability_exposes_exact_configured_commands():
    server = DevServerCommand(
        Path("web"),
        sys.executable,
        ("-m", "http.server"),
        None,
        "http://localhost:8000",
        5,
    )
    config = {
        "tester": {
            "dev_server": [server],
            "browser": {
                "kind": "command",
                "name": "playwright",
                "entrypoint": "npx playwright test",
                "required": True,
            },
        }
    }
    result = browser_capability(config)
    assert result.configured
    assert result.start_command == sys.executable
    assert result.readiness_url == "http://localhost:8000"
    assert result.provider == "npx playwright test"
    assert result.required

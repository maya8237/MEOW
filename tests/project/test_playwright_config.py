import pytest

from meow.project.command_detection import MAX_MANIFEST_BYTES
from meow.project.playwright_config import ServerEntry, read_playwright_config


def write(root, text, name="playwright.config.ts"):
    (root / name).write_text(text, encoding="utf-8")


def test_no_config_returns_an_empty_result(tmp_path):
    config = read_playwright_config(tmp_path)
    assert config.file is None
    assert config.servers == ()
    assert config.unreadable == ()


@pytest.mark.parametrize(
    "name",
    [
        "playwright.config.ts",
        "playwright.config.js",
        "playwright.config.mjs",
        "playwright.config.cjs",
    ],
)
def test_every_extension_is_found(tmp_path, name):
    write(tmp_path, "export default { use: { baseURL: 'http://x:1' } }", name)
    config = read_playwright_config(tmp_path)
    assert config.file == name
    assert config.base_url == "http://x:1"


def test_object_web_server_with_literal_timeout(tmp_path):
    write(
        tmp_path,
        """
import { defineConfig } from '@playwright/test';
export default defineConfig({
  webServer: {
    command: 'npm run dev',
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    cwd: 'apps/web',
  },
});
""",
    )
    assert read_playwright_config(tmp_path).servers == (
        ServerEntry("npm run dev", "http://localhost:3000", None, "apps/web", 120.0),
    )


def test_array_web_server_and_port_only_entry(tmp_path):
    write(
        tmp_path,
        "export default { webServer: [ { command: 'a', port: 3000 },"
        " { command: 'b', url: 'http://localhost:4000' } ] }",
    )
    servers = read_playwright_config(tmp_path).servers
    assert servers == (
        ServerEntry("a", None, 3000, ".", None),
        ServerEntry("b", "http://localhost:4000", None, ".", None),
    )


def test_channels_executable_and_base_url_from_use_and_projects(tmp_path):
    write(
        tmp_path,
        """
export default {
  use: { baseURL: 'http://localhost:5173', channel: 'chrome' },
  projects: [
    { name: 'a', use: { channel: 'msedge' } },
    { name: 'b', use: { launchOptions: { executablePath: '/usr/bin/chrome' } } },
  ],
};
""",
    )
    config = read_playwright_config(tmp_path)
    assert config.base_url == "http://localhost:5173"
    assert config.channels == frozenset({"chrome", "msedge"})
    assert config.executable == "/usr/bin/chrome"


def test_non_literal_values_are_reported_and_not_inferred(tmp_path):
    write(
        tmp_path,
        """
export default {
  webServer: { command: `${process.env.CMD} run dev`, url: 'http://localhost:3000' },
  use: { baseURL: process.env.BASE_URL || 'http://localhost:3000', channel: browser },
};
""",
    )
    config = read_playwright_config(tmp_path)
    assert config.servers == ()
    assert config.base_url is None
    assert config.channels == frozenset()
    assert any("webServer.command" in item for item in config.unreadable)
    assert any("baseURL" in item for item in config.unreadable)
    assert any("channel" in item for item in config.unreadable)


def test_computed_timeout_is_reported_but_the_server_is_kept(tmp_path):
    write(
        tmp_path,
        "export default { webServer: { command: 'x', port: 1, timeout: 120 * 1000 } }",
    )
    config = read_playwright_config(tmp_path)
    assert config.servers == (ServerEntry("x", None, 1, ".", None),)
    assert any("timeout" in item for item in config.unreadable)


def test_comments_and_strings_do_not_create_matches(tmp_path):
    write(
        tmp_path,
        """
// webServer: { command: 'bad', port: 1 }
/* channel: 'chrome' */
export default { title: "channel: 'chrome' webServer: { command: 'x' }" };
""",
    )
    config = read_playwright_config(tmp_path)
    assert config.servers == ()
    assert config.channels == frozenset()


def test_symlink_oversize_and_malformed_files_are_ignored(tmp_path):
    real = tmp_path / "real.ts"
    real.write_text("export default { use: { baseURL: 'http://x:1' } }")
    try:
        (tmp_path / "playwright.config.ts").symlink_to(real)
    except OSError:
        pass  # symlinks need a Windows privilege; the other cases still run
    else:
        assert read_playwright_config(tmp_path).file is None
        (tmp_path / "playwright.config.ts").unlink()
    write(tmp_path, "x" * (MAX_MANIFEST_BYTES + 1))
    assert read_playwright_config(tmp_path).file is None
    write(tmp_path, "export default { webServer: { command: 'x', port: 1 ")
    config = read_playwright_config(tmp_path)
    assert config.servers == ()

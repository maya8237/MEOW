"""Run the POSIX installer with local stand-ins for downloads and Python."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX installer")


def _executable(directory, name, content):
    path = directory / name
    path.write_text("#!/bin/sh\nset -eu\n" + content)
    path.chmod(0o755)
    return path


def _run_installer(tmp_path, flags):
    tools = tmp_path / "tools"
    tools.mkdir()
    _executable(tools, "uv", "exit 1\n")
    _executable(
        tools,
        "curl",
        """cat <<'JSON'
{
  "tag_name": "1.2.3",
  "wheel_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
JSON
""",
    )
    python = _executable(
        tools,
        "python",
        """case "$1" in
    -c)
        case "$2" in
            *sysconfig*) printf '%s\\n' "$HOME/purelib" ;;
            *) printf '31300 %s\\n' "$0" ;;
        esac ;;
    -m)
        case "$2" in
            venv) mkdir -p "$3/bin"; cp "$0" "$3/bin/python3" ;;
            pip) : ;;
            meow.installer.releases)
                case "$3" in
                    --finish)
                        mkdir -p "$4/runtime/bin"
                        cp "$0" "$4/runtime/bin/python3" ;;
                    --activate) printf '%s\\n' "$*" > "$HOME/activation" ;;
                esac ;;
        esac ;;
esac
""",
    )
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "MEOW_PYTHON": str(python),
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
    }
    argv = [shutil.which("sh"), str(ROOT / "scripts/install.sh"), *flags]
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return (tmp_path / "activation").read_text()


@pytest.mark.parametrize("setup", [False, True])
@pytest.mark.parametrize("non_interactive", [False, True])
def test_installer_switch_controls_setup_without_network(
    tmp_path, non_interactive, setup
):
    flags = [
        *(["--non-interactive"] if non_interactive else []),
        *(["--setup"] if setup else []),
    ]
    activation = _run_installer(tmp_path, flags)
    assert ("--setup" in activation) is (setup and not non_interactive)
    assert "--activate" in activation


@pytest.mark.parametrize(
    "flags,expected",
    [
        (["--mcp", "jira,github"], "--mcp jira,github"),
        (["--mcp=jira"], "--mcp jira"),
        (["--setup", "--mcp", "github"], "--mcp github"),
        ([], None),
    ],
)
def test_installer_forwards_mcp_providers(tmp_path, flags, expected):
    activation = _run_installer(tmp_path, flags)
    assert (expected in activation) if expected else ("--mcp" not in activation)


def test_mcp_flag_needs_a_value(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/install.sh"), "--mcp"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not (tmp_path / ".meow").exists()


def test_help_and_unknown_flags_do_not_install(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    script = str(ROOT / "scripts/install.sh")
    for flag, expected in (("--help", 0), ("--non-interactiv", 2)):
        result = subprocess.run(
            ["sh", script, flag], env=env, capture_output=True, text=True, timeout=10
        )
        assert result.returncode == expected
        assert not (tmp_path / ".meow").exists()

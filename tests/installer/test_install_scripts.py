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


def _run_installer(tmp_path, flags, *, latest_pypi=False, pypi_mode="simple"):
    tools = tmp_path / "tools"
    tools.mkdir()
    _executable(tools, "uv", "exit 1\n")
    simple_response = (
        "exit 22"
        if pypi_mode == "error"
        else """cat <<'JSON'
{
  "files": []
}
JSON"""
        if pypi_mode == "empty"
        else """cat <<'JSON'
{
  "files": [
    {
      "filename": "meow_harness-9.9.9-py3-none-any.whl",
      "hashes": {
        "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      },
      "yanked": false
    }
  ]
}
JSON"""
    )
    legacy_response = """cat <<'JSON'
{
  "info": {"version": "8.8.8"},
  "urls": [
    {
      "packagetype": "bdist_wheel",
      "yanked": false,
      "digests": {
        "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      }
    }
  ]
}
JSON"""
    _executable(
        tools,
        "curl",
        f"""case "$*" in
    *pypi.org/simple/meow-harness/*)
        {simple_response}
        ;;
    *pypi.org/pypi/meow-harness/json*)
        {legacy_response}
        ;;
    *)
        cat <<'JSON'
{{
            "tag_name": "1.2.3",
  "wheel_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}}
JSON
        ;;
esac
""",
    )
    python = _executable(
        tools,
        "python",
        """case "$1" in
    -c)
        case "$2" in
            *sysconfig*) printf '%s\\n' "$HOME/purelib" ;;
            *files*)
                printf '9.9.9 '
                printf '%s%s\\n' \
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' \
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                ;;
            *urls*)
                printf '8.8.8 '
                printf '%s%s\\n' \
                    'cccccccccccccccccccccccccccccccc' \
                    'cccccccccccccccccccccccccccccccc'
                ;;
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


@pytest.mark.parametrize(
    "flags,asks",
    [
        ([], False),  # no terminal in the test run: the questions are skipped
        (["--no-setup"], False),
        (["--non-interactive"], False),
        (["--setup", "--non-interactive"], False),
    ],
)
def test_installer_skips_questions_without_terminal_or_with_switches(
    tmp_path, flags, asks
):
    activation = _run_installer(tmp_path, flags)
    assert ("--setup" in activation) is asks
    assert "--activate" in activation


def test_installer_forwards_bun_without_asking(tmp_path):
    activation = _run_installer(tmp_path, ["--non-interactive", "--bun"])
    assert "--bun" in activation
    assert "--setup" not in activation


@pytest.mark.parametrize(
    "flags,expected",
    [
        (["--mcp", "jira,gitlab"], "--mcp jira,gitlab"),
        (["--mcp=jira"], "--mcp jira"),
        (["--setup", "--mcp", "gitlab"], "--mcp gitlab"),
        ([], None),
    ],
)
def test_installer_forwards_mcp_providers(tmp_path, flags, expected):
    activation = _run_installer(tmp_path, flags)
    assert (expected in activation) if expected else ("--mcp" not in activation)


def test_installer_can_select_newest_stable_pypi_release(tmp_path):
    activation = _run_installer(
        tmp_path, ["--non-interactive", "--latest-pypi"], latest_pypi=True
    )
    assert "9.9.9" in activation
    assert "1.2.3" not in activation


@pytest.mark.parametrize("pypi_mode", ["error", "empty"])
def test_installer_falls_back_to_legacy_pypi_json(tmp_path, pypi_mode):
    activation = _run_installer(
        tmp_path,
        ["--non-interactive", "--latest-pypi"],
        latest_pypi=True,
        pypi_mode=pypi_mode,
    )
    assert "8.8.8" in activation
    assert "9.9.9" not in activation


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

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _bash_path() -> Path:
    return Path("C:/Program Files/Git/bin/bash.exe")


def _git_bash_path(path: Path) -> str:
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:]


def _run_posix_installer(
    tmp_path: Path,
    *,
    meow_version: str | None,
    origin_version: str | None,
    input_text: str = "",
) -> tuple[subprocess.CompletedProcess[str], str]:
    bash = _bash_path()
    if not bash.is_file():
        pytest.skip("Git Bash is required for bootstrap integration tests")

    log = tmp_path / "commands.log"
    wrapper = tmp_path / "run-installer.sh"
    functions = [
        "#!/usr/bin/env bash",
        "git() {",
        "  printf 'git %s\\n' \"$*\" >> \"$MEOW_TEST_LOG\"",
        "  if [ \"$1\" = clone ]; then mkdir -p \"$3/.git\"; fi",
        "}",
        "function python3.15 {",
        "  printf 'python %s\\n' \"$*\" >> \"$MEOW_TEST_LOG\"",
        "  case \"$*\" in",
        "    *'-m pip install -e'*) printf 'FAKE_PIP\\n' >&2 ;;",
        "    *'-m meow.installer'*) printf 'FAKE_SETUP\\n' >&2 ;;",
        "  esac",
        "}",
    ]
    if meow_version is not None:
        functions.extend(
            [
                "meow() {",
                f"  printf 'meow {meow_version}\\n'",
                "}",
            ]
        )
    if origin_version is not None:
        functions.extend(
            [
                "curl() {",
                "  printf '[project]\\n'",
                f"  printf 'version = \"{origin_version}\"\\n'",
                "}",
            ]
        )
    functions.extend(
        [
            "export PATH='/usr/bin:/bin'",
            f"export HOME='{_git_bash_path(tmp_path / 'home')}'",
            f"export MEOW_TEST_LOG='{_git_bash_path(log)}'",
            f"source '{_git_bash_path(ROOT / 'scripts' / 'install.sh')}'",
        ]
    )
    wrapper.write_text("\n".join(functions) + "\n", encoding="utf-8")
    env = dict(os.environ)
    result = subprocess.run(
        [str(bash), str(wrapper)],
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_bootstrap_scripts_use_ssh_clone_and_installed_runtime():
    powershell = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    posix = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    for script in (powershell, posix):
        assert "git@github.com:maya8237/MEOW.git" in script
        assert "pip install -e" in script
        assert "meow.installer" in script

    assert "C:/Projects" in powershell
    assert "$HOME" in posix
    assert "$MyInvocation.MyCommand.Path" not in powershell


def test_bootstrap_scripts_stop_when_meow_is_already_on_path():
    powershell = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    posix = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "Get-Command meow" in powershell
    assert "command -v meow" in posix
    for script in (powershell, posix):
        assert "origin/main" in script
        assert "older" in script.lower()
        assert "pip install -e" in script


def test_posix_bootstrap_uses_existing_meow_and_skips_install_when_older(
    tmp_path,
):
    result, _log = _run_posix_installer(
        tmp_path,
        meow_version="0.1.0",
        origin_version="0.2.0",
    )

    assert result.returncode == 0, result.stderr
    assert "older" in result.stderr.lower()
    assert "0.1.0" in result.stderr
    assert "0.2.0" in result.stderr
    assert "FAKE_PIP" not in result.stderr
    assert "FAKE_SETUP" not in result.stderr
    assert "Cloning MEOW" not in result.stdout


def test_posix_bootstrap_installs_when_meow_is_not_on_path(tmp_path):
    result, _log = _run_posix_installer(
        tmp_path,
        meow_version=None,
        origin_version=None,
        input_text="\n",
    )

    assert result.returncode == 0, result.stderr
    assert "FAKE_PIP" in result.stderr
    assert "FAKE_SETUP" in result.stderr


def test_posix_bootstrap_passes_shell_syntax():
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    if not bash.is_file():
        return

    result = subprocess.run(
        [str(bash), "-n", str(ROOT / "scripts" / "install.sh")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_readme_starts_with_streamed_install_commands():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    powershell_command = (
        "irm https://raw.githubusercontent.com/"
        "maya8237/MEOW/main/scripts/install.ps1 | iex"
    )
    posix_command = (
        "curl -fsSL https://raw.githubusercontent.com/"
        "maya8237/MEOW/main/scripts/install.sh | sh"
    )

    assert powershell_command in text
    assert posix_command in text
    assert text.count("## Install") == 1
    assert "## Run" in text
    assert text.index(powershell_command) < text.index("## Run")
    assert text.index(posix_command) < text.index("## Run")
    assert text.count("/meow:run") == 1
    assert text.count("meow run") == 1
    assert "pip install" not in text.lower()
    assert "claude plugin marketplace" not in text.lower()
    assert "The installer" not in text


def test_powershell_trims_destination_separators_as_characters():
    text = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "TrimEnd([char[]]@(" in text

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _bash_path() -> Path:
    return Path("C:/Program Files/Git/bin/bash.exe")


def _git_bash_path(path: Path) -> str:
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_posix_installer(  # ruff: ignore[too-many-arguments]
    tmp_path: Path,
    *,
    meow_version: str | None,
    origin_version: str | None,
    existing_checkout: Path | None = None,
    input_text: str = "",
    setup_exit_code: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bash = _bash_path()
    if not bash.is_file():
        pytest.skip("Git Bash is required for bootstrap integration tests")

    log = tmp_path / "commands.log"
    wrapper = tmp_path / "run-installer.sh"
    install_script = _git_bash_path(ROOT / "scripts" / "install.sh")
    existing_checkout_value = (
        _git_bash_path(existing_checkout) if existing_checkout else ""
    )
    if existing_checkout is not None:
        (existing_checkout / ".git").mkdir(parents=True)
        (existing_checkout / "skills").mkdir()
    functions = [
        "#!/usr/bin/env bash",
        "git() {",
        "  printf 'git %s\\n' \"$*\" >> \"$MEOW_TEST_LOG\"",
        "  if [ \"$1\" = clone ]; then mkdir -p \"$3/.git\"; fi",
        "}",
        "function python3.15 {",
        "  printf 'python %s\\n' \"$*\" >> \"$MEOW_TEST_LOG\"",
            "  case \"$*\" in",
            "    *'import meow'*) printf '%s\\n' \"$MEOW_EXISTING_CHECKOUT\" ;;",
            "    *'-m pip install -e'*) printf 'FAKE_PIP\\n' >&2 ;;",
            "    *'-m meow.installer'*) printf 'FAKE_SETUP\\n' >&2; return \"$MEOW_SETUP_EXIT\" ;;",
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
            f"export MEOW_EXISTING_CHECKOUT='{existing_checkout_value}'",
            f"export MEOW_SETUP_EXIT='{setup_exit_code}'",
            f"printf '%s' \"$MEOW_TEST_INPUT\" | source '{install_script}'",
        ]
    )
    wrapper.write_text("\n".join(functions) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["MEOW_TEST_INPUT"] = input_text
    result = subprocess.run(
        [str(bash), str(wrapper)],
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

    assert "Find-ExistingMeowCheckout" in powershell
    assert "find_existing_checkout" in posix
    assert "ForegroundColor Green" in powershell
    assert "ForegroundColor Red" in powershell
    assert "\\033[32mDone!" in posix
    assert "\\033[31mFailed:" in posix


def test_posix_bootstrap_uses_existing_meow_and_skips_install_when_older(
    tmp_path,
):
    existing_checkout = tmp_path / "existing-meow"
    result, log = _run_posix_installer(
        tmp_path,
        meow_version="0.1.0",
        origin_version="0.2.0",
        existing_checkout=existing_checkout,
        input_text="n\n",
    )

    assert result.returncode == 0, result.stderr
    assert "older" in result.stderr.lower()
    assert "0.1.0" in result.stderr
    assert "0.2.0" in result.stderr
    assert "FAKE_PIP" not in result.stderr
    assert "MEOW was not updated" in result.stdout
    assert "git -C" in result.stdout
    assert "pull --ff-only origin main" in result.stdout
    assert "python3.15 -m pip install -e" in result.stdout
    assert "FAKE_SETUP" in result.stderr
    assert "-m meow.installer" in log
    assert "Cloning MEOW" not in result.stdout
    assert "\033[32mDone!\033[0m" in result.stdout


def test_posix_bootstrap_warns_when_existing_checkout_cannot_be_located(tmp_path):
    result, log = _run_posix_installer(
        tmp_path,
        meow_version="0.1.0",
        origin_version="0.2.0",
    )

    assert result.returncode == 0, result.stderr
    assert "checkout" in result.stderr.lower()
    assert "FAKE_PIP" not in result.stderr
    assert "FAKE_SETUP" not in result.stderr
    assert "-m meow.installer" not in log


def test_posix_bootstrap_updates_existing_meow_when_approved(tmp_path):
    existing_checkout = tmp_path / "existing-meow"
    result, log = _run_posix_installer(
        tmp_path,
        meow_version="0.1.0",
        origin_version="0.2.0",
        existing_checkout=existing_checkout,
        input_text="y\n",
    )

    assert result.returncode == 0, result.stderr
    assert "MEOW updated successfully" in result.stdout
    assert "git -C" in log
    assert "pull --ff-only origin main" in log
    assert "FAKE_PIP" in result.stderr
    assert "-m pip install -e" in log
    assert "FAKE_SETUP" in result.stderr


def test_posix_bootstrap_reports_failure_in_red(tmp_path):
    existing_checkout = tmp_path / "existing-meow"
    result, _log = _run_posix_installer(
        tmp_path,
        meow_version="0.1.0",
        origin_version="0.2.0",
        existing_checkout=existing_checkout,
        input_text="n\n",
        setup_exit_code=1,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "\033[31mFailed:" in output
    assert "Done!" not in output


@pytest.mark.skipif(sys.platform != "linux", reason="Linux installer integration test")
def test_linux_install_script_runs_from_ci_checkout_without_cloning(  # ruff: ignore[too-many-statements]
    tmp_path,
):
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()

    python_wrapper = "#!/bin/sh\nexec \"$MEOW_TEST_PYTHON\" \"$@\"\n"
    # install.sh tries python3.15 ... python3.12 before python3; without a
    # wrapper for each, /bin/python3.12 (the runner's system Python, which
    # lacks MEOW's dependencies) would be picked instead.
    for name in ("python3.15", "python3.14", "python3.13", "python3.12", "python3"):
        _write_executable(bin_dir / name, python_wrapper)
    _write_executable(
        bin_dir / "meow",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        "  printf 'meow 0.1.0\\n'\n"
        "fi\n",
    )
    _write_executable(
        bin_dir / "curl",
        "#!/bin/sh\n"
        "printf '[project]\\nversion = \"0.2.0\"\\n'\n",
    )

    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}:/bin",
        HOME=str(home),
        MEOW_TEST_PYTHON=sys.executable,
        PYTHONPATH=str(ROOT / "src"),
    )
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "scripts" / "install.sh")],
        cwd=ROOT,
        input="n\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert (
        "Configuring Claude and onboarding projects using existing MEOW checkout"
        in output
    )
    assert str(ROOT) in output
    assert "MEOW was not updated" in output
    assert "git -C" in output
    assert "pull --ff-only origin main" in output
    assert "Updating MEOW from existing checkout" not in output
    assert "Cloning MEOW" not in output
    assert "pip install -e" in output
    assert "\033[32mDone!\033[0m" in output


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows installer integration test"
)
def test_windows_install_script_runs_from_ci_checkout_without_cloning(  # ruff: ignore[too-many-statements]
    tmp_path,
):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows installer test")

    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    (bin_dir / "meow.cmd").write_text(
        "@echo off\n"
        "if /I \"%~1\"==\"--version\" (\n"
        "  echo meow 0.1.0\n"
        "  exit /b 0\n"
        ")\n"
        "exit /b 0\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(
        PATH=os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent)]),
        HOME=str(home),
        USERPROFILE=str(home),
        PYTHONPATH=str(ROOT / "src"),
    )
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "install.ps1"),
        ],
        cwd=ROOT,
        input="n\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert (
        "Configuring Claude and onboarding projects using existing MEOW checkout"
        in output
    )
    assert str(ROOT) in output
    assert "MEOW was not updated" in output
    assert "git -C" in output
    assert "pull --ff-only origin main" in output
    assert "Updating MEOW from existing checkout" not in output
    assert "Cloning MEOW" not in output
    assert "pip install -e" in output
    assert "Done!" in output


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
    assert "Done!" in result.stdout


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

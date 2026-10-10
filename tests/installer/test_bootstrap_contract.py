import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_scripts_use_ssh_clone_and_installed_runtime():
    powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
    posix = (ROOT / "install.sh").read_text(encoding="utf-8")

    for script in (powershell, posix):
        assert "git@github.com:maya8237/MEOW.git" in script
        assert "pip install -e" in script
        assert "meow.installer" in script

    assert "C:/Projects" in powershell
    assert "$HOME" in posix
    assert "$MyInvocation.MyCommand.Path" not in powershell


def test_posix_bootstrap_passes_shell_syntax():
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    if not bash.is_file():
        return

    result = subprocess.run(
        [str(bash), "-n", str(ROOT / "install.sh")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_readme_starts_with_streamed_install_commands():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    powershell_command = (
        "irm https://raw.githubusercontent.com/maya8237/MEOW/main/install.ps1 | iex"
    )
    posix_command = (
        "curl -fsSL https://raw.githubusercontent.com/"
        "maya8237/MEOW/main/install.sh | sh"
    )

    assert powershell_command in text
    assert posix_command in text
    existing_install = text.index(
        "## Install\n\n### Install the Claude Code plugin"
    )
    assert text.index(powershell_command) < existing_install
    assert text.index(posix_command) < existing_install
    assert "/meow:onboard" in text
    assert 'meow run "Add CSV export" --name csv-export --work-dir <project>' in text
    assert "/meow:run Add CSV export" in text
    assert "does not\nfinish with a menu" in text


def test_powershell_trims_destination_separators_as_characters():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "TrimEnd([char[]]@(" in text

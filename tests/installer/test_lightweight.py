"""Default installation and update must never enter optional setup."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from meow.installer import _runtime


@pytest.mark.parametrize(
    "flags", [[], ["--non-interactive"], ["--setup", "--non-interactive"]]
)
@pytest.mark.parametrize("interactive", [False, True])
def test_registration_only_default(tmp_path, monkeypatch, flags, interactive):
    registered = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(
        _runtime, "register_checkout_plugin", lambda *args: registered.append(args)
    )

    def unexpected(*args):
        pytest.fail("optional setup, input, or repository scan attempted")

    for name in ("_setup_projects", "_saved", "start_bun_migration", "onboard_project"):
        monkeypatch.setattr(_runtime, name, unexpected)
    monkeypatch.setattr("builtins.input", unexpected)
    assert _runtime.main(["--repo-dir", str(tmp_path), *flags]) == 0
    assert len(registered) == 1
    assert registered[0][1] == tmp_path


def test_noninteractive_bun_helper_skips_optional_work(monkeypatch):
    monkeypatch.setattr(
        _runtime, "_update_bun", lambda: pytest.fail("optional update setup")
    )
    assert _runtime.main(["--update-bun", "--non-interactive"]) == 0


def test_powershell_setup_is_explicit():
    script = (Path(__file__).resolve().parents[2] / "scripts/install.ps1").read_text()
    assert "[switch]$setup" in script
    assert "if ($setup -and -not ${non-interactive})" in script


@pytest.mark.parametrize("setup", [False, True])
@pytest.mark.parametrize("non_interactive", [False, True])
def test_powershell_activation_arguments(setup, non_interactive):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell unavailable")
    script = (Path(__file__).resolve().parents[2] / "scripts/install.ps1").read_text()
    # Execute only the argument construction, never downloads or installation.
    start = script.index("        $setupArgs =")
    end = script.index("        & (Join-Path $destination", start)
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            f"$setup = ${str(setup).lower()}; "
            f"${{non-interactive}} = ${str(non_interactive).lower()}; "
            "$destination = 'release'; " + script[start:end] + "; $setupArgs",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert result.stdout.splitlines() == [
        "--activate",
        "release",
        *(["--setup"] if setup and not non_interactive else []),
    ]


def test_posix_default_branch_only_activates():
    script = (Path(__file__).resolve().parents[2] / "scripts/install.sh").read_text()
    assert "setup=false" in script
    assert "--setup) setup=true ;;" in script
    default = script.split(
        'if [ "$setup" = false ] || [ "$non_interactive" = true ]; then', 1
    )[1]
    default = default.split("elif", 1)[0]
    assert '--activate "$destination"' in default
    assert "--setup" not in default

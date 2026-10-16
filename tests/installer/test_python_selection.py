"""Exercise installer selection with crashing and unresponsive interpreters."""

import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.name != "nt", reason="Windows upgrade subprocesses")
def test_powershell_removes_old_install_before_installing_new_python(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    functions = "\n".join(
        re.search(rf"    function {name} \{{.*?^    \}}", source, re.M | re.S).group()
        for name in (
            "Invoke-WithSpinner",
            "Remove-PreviousMeow",
            "Invoke-Maintenance",
            "Install-ExistingCheckout",
        )
    )
    fake = tmp_path / "fake python.py"
    log = tmp_path / "commands.log"
    removed = tmp_path / "removed"
    fake.write_text(
        "import sys\nfrom pathlib import Path\n"
        "environment, log, removed = sys.argv[1:4]\n"
        "args = sys.argv[4:]\n"
        "if '--install' in args:\n"
        "    with open(log, 'a') as stream:\n"
        "        stream.write(environment + ' -m pip install -e ' + args[-2] + '\\n')\n"
        "elif args[0] == '-c':\n"
        "    if environment != 'old' or Path(removed).exists(): sys.exit(1)\n"
        "    print('old-distribution')\n"
        "else:\n"
        "    with open(log, 'a') as stream:\n"
        "        stream.write(environment + ' ' + ' '.join(args) + '\\n')\n"
        "    if 'uninstall' in args: Path(removed).touch()\n"
    )
    wrapper = tmp_path / "upgrade.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n"
        + functions
        + "\nfunction Test-Python312 { param($Executable, $Arguments) "
        "return ($Arguments[1] -ne 'broken') }\n"
        "function Get-PythonCandidates { return $candidates }\n"
        "$candidates=@('old','new','broken') | ForEach-Object { "
        f"@{{Executable='{sys.executable}'; "
        f"Arguments=@('{fake}', $_, '{log}', '{removed}')}} }}\n"
        "$setup=$candidates[1]\n"
        f"$setup.Checkout='{tmp_path / 'checkout with spaces'}'\n"
        "Install-ExistingCheckout -Setup $setup\n"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == [
        "old -m pip uninstall -y meow",
        f"new -m pip install -e {tmp_path / 'checkout with spaces'}",
    ]
    assert "broken Python" not in result.stdout


@pytest.mark.parametrize("broken", ["crash", "hang", "all"])
def test_posix_python_selection_skips_broken_candidates(tmp_path, broken):
    bash = shutil.which("bash")
    if os.name == "nt":
        bash = "C:/Program Files/Git/bin/bash.exe"
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash required")
    source = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    functions = source[
        source.index("is_supported_python()") : source.index("# Sets existing_python")
    ]
    first = "exec sleep 5" if broken == "hang" else "return 1"
    second = "return 1" if broken == "all" else "printf '3.14.0\\n'"
    wrapper = tmp_path / "selection.sh"
    wrapper.write_text(
        "python_probe_timeout=1\npython_candidates='broken good'\n"
        f"broken() {{ {first}; }}\ngood() {{ {second}; }}\n"
        + functions
        + "\npython_paths() { printf 'broken\\ngood\\n'; }\nselect_python\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [bash, str(wrapper)], capture_output=True, text=True, timeout=4, check=False
    )
    assert result.returncode == (1 if broken == "all" else 0)
    assert result.stdout.strip() == ("" if broken == "all" else "good")
    assert "warning" in result.stderr.lower()
    assert "broken" in result.stderr


@pytest.mark.parametrize("broken", ["crash", "hang", "all"])
def test_powershell_python_selection_skips_broken_candidates(tmp_path, broken):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell or os.name != "nt":
        pytest.skip("Windows PowerShell required")
    source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    functions = source[
        source.index("    function Test-Python312") : source.index(
            "    function Format-PythonCommand"
        )
    ]
    functions = re.sub(
        r"    function Get-PythonCandidates \{.*?^    \}",
        "    function Get-PythonCandidates { return $candidates }",
        functions,
        flags=re.M | re.S,
    )
    fake = tmp_path / "fake python.py"
    fake.write_text(
        "import sys, time\n"
        "if sys.argv[1] == 'hang': time.sleep(5)\n"
        "if sys.argv[1] == 'good': print('3.14.0')\n"
        "sys.exit(0 if sys.argv[1] == 'good' else 1)\n",
        encoding="utf-8",
    )
    second = "crash" if broken == "all" else "good"
    wrapper = tmp_path / "selection.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n$pythonProbeTimeoutMs = 1000\n"
        f"$candidates = @(@{{Executable='{sys.executable}'; "
        f"Arguments=@('\"{fake}\"', '{broken}')}},"
        f"@{{Executable='{sys.executable}'; Arguments=@('\"{fake}\"', '{second}')}})\n"
        + functions
        + "\ntry { $selected = Select-Python; Write-Output $selected.Arguments[-1] } "
        "catch { exit 1 }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=7,
        check=False,
    )
    assert result.returncode == (1 if broken == "all" else 0)
    assert "WARNING" in result.stdout
    if broken != "all":
        assert result.stdout.strip().endswith("good")


def test_posix_selects_newest_python_across_path_entries(tmp_path):
    bash = (
        "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    )
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash required")
    paths = []
    for folder, version in (("older python", "3.12.9"), ("newer python", "3.15.1")):
        directory = tmp_path / folder
        directory.mkdir()
        executable = directory / "python"
        executable.write_text(f"#!/bin/sh\nprintf '{version}\\n'\n")
        executable.chmod(0o755)
        value = directory.as_posix()
        paths.append("/" + value[0].lower() + value[2:] if os.name == "nt" else value)
    source = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    functions = source[
        source.index("is_supported_python()") : source.index("# Sets existing_python")
    ]
    wrapper = tmp_path / "latest.sh"
    wrapper.write_text(
        "python_probe_timeout=1\npython_candidates='python'\n"
        f"PATH='{':'.join(paths)}:/usr/bin:/bin'\n" + functions + "\nselect_python\n"
    )
    result = subprocess.run(
        [bash, str(wrapper)], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == paths[1] + "/python"


@pytest.mark.skipif(os.name != "nt", reason="Windows where.exe discovery")
def test_powershell_discovers_all_python_paths_without_py_launcher(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    directories = [tmp_path / "older", tmp_path / "newer"]
    for directory in directories:
        directory.mkdir()
        (directory / "python.exe").touch()
        (directory / "py.exe").touch()
    source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    function = re.search(
        r"    function Get-PythonCandidates \{.*?^    \}", source, re.M | re.S
    ).group()
    wrapper = tmp_path / "where.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$env:PATH='{';'.join(map(str, directories))}'\n"
        + function
        + "\nGet-PythonCandidates | ForEach-Object { $_.Executable }\n"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str(directory / "python.exe") for directory in directories
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows Python subprocesses")
def test_powershell_selects_highest_healthy_version(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    fake = tmp_path / "version.py"
    fake.write_text("import sys\nprint(sys.argv[1])\n")
    source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    functions = source[
        source.index("    function Test-Python312") : source.index(
            "    function Format-PythonCommand"
        )
    ]
    functions = re.sub(
        r"    function Get-PythonCandidates \{.*?^    \}",
        "    function Get-PythonCandidates { return $candidates }",
        functions,
        flags=re.M | re.S,
    )
    wrapper = tmp_path / "latest.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n$pythonProbeTimeoutMs=1000\n"
        "$candidates=@("
        + ",".join(
            f"@{{Executable='{sys.executable}'; "
            f"Arguments=@('\"{fake}\"', '{version}')}}"
            for version in ("3.12.9", "3.15.1", "3.14.2")
        )
        + ")\n"
        + functions
        + "\n(Select-Python).Version.ToString()\n"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=7,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3.15.1"


def test_posix_warns_once_across_discovery_selection_and_cleanup(tmp_path):
    bash = (
        "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    )
    source = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    functions = source[
        source.index("is_supported_python()") : source.index("# Prints how")
    ]
    wrapper = tmp_path / "warnings.sh"
    wrapper.write_text(
        "python_probe_timeout=1\npython_candidates='broken good'\n"
        "broken() { return 1; }\n"
        "good() { case \"$*\" in *runpy.run_module*) printf '3.14.0\\n';; "
        "*) return 1;; esac; }\n"
        + functions
        + "\npython_paths() { printf 'broken\\ngood\\n'; }\n"
        "find_existing_checkout || true\nselect_python\nremove_previous_meow\n"
    )
    result = subprocess.run(
        [bash, str(wrapper)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "good"
    assert result.stderr.count("Warning:") == 1, result.stderr
    assert "broken" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows Python subprocesses")
def test_powershell_warns_once_across_discovery_selection_and_cleanup(tmp_path):
    source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    functions = "\n".join(
        re.search(rf"    function {name} \{{.*?^    \}}", source, re.M | re.S).group()
        for name in (
            "Test-Python312",
            "Select-Python",
            "Find-ExistingMeowCheckout",
            "Remove-PreviousMeow",
        )
    )
    fake = tmp_path / "probe.py"
    fake.write_text(
        "import sys\n"
        "if sys.argv[1] == 'good' and 'runpy.run_module' in sys.argv[-1]:\n"
        "    print('3.14.0'); sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    wrapper = tmp_path / "warnings.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n$pythonProbeTimeoutMs=1000\n"
        + functions
        + "\nfunction Get-PythonCandidates { return $candidates }\n"
        "$candidates=@('broken','good') | ForEach-Object { "
        f"@{{Executable='{sys.executable}'; Arguments=@('\"{fake}\"', $_)}} }}\n"
        "$null=Find-ExistingMeowCheckout\n"
        "(Select-Python).Arguments[-1]\nRemove-PreviousMeow\n"
    )
    result = subprocess.run(
        [
            shutil.which("powershell"),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("WARNING:") == 1, result.stdout
    assert "good" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows Python subprocesses")
def test_powershell_rejects_real_interpreter_without_pip(tmp_path):
    environment = tmp_path / "python without pip"
    venv.create(environment, with_pip=False)
    broken = environment / "Scripts/python.exe"
    source = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    functions = "\n".join(
        re.search(rf"    function {name} \{{.*?^    \}}", source, re.M | re.S).group()
        for name in ("Test-Python312", "Select-Python")
    )
    wrapper = tmp_path / "missing-pip.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n$pythonProbeTimeoutMs=10000\n"
        + functions
        + "\nfunction Get-PythonCandidates { "
        f"@{{Executable='{broken}'; Arguments=@()}}; "
        f"@{{Executable='{sys.executable}'; Arguments=@()}} }}\n"
        "(Select-Python).Executable\n"
    )
    result = subprocess.run(
        [
            shutil.which("powershell"),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith(sys.executable)
    assert result.stdout.count("WARNING:") == 1
    assert str(broken) in result.stdout.replace("\n", "")

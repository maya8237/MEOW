"""Install this checkout's wheel through the real installers, like users do."""

import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_EXIT_CODE = 7
SMOKE_PATCH_OFFSET = 100_000  # keeps the smoke version clear of anything published
sys.path.insert(0, str(ROOT / "src"))

from meow.installer.releases import release_python  # ruff: ignore[module-import-not-at-top-of-file]


class ReleaseServer(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/latest.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            manifest = {
                "tag_name": self.server.tag,
                "wheel_sha256": self.server.digest,
            }
            self.wfile.write(json.dumps(manifest, indent=2).encode())
        else:
            super().do_GET()


def snapshot(destination: Path, version: str) -> None:
    """Copy the tracked checkout to DESTINATION, stamped with an unpublished VERSION.

    Once a release is on PyPI, pip prefers its same-numbered wheel over the
    freshly built one and fails the hash check, so the smoke wheel needs its own.
    """
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    for name in filter(None, names):
        if (ROOT / name).is_file():
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination / name)
    project = destination / "pyproject.toml"
    project.write_text(
        re.sub(
            r"(?m)^version = .*$",
            f'version = "{version}"',
            project.read_text(),
            count=1,
        )
    )
    for manifest in (destination / ".claude-plugin").glob("*.json"):
        data = json.loads(manifest.read_text())
        target = data if "version" in data else data["plugins"][0]
        target["version"] = version
        manifest.write_text(json.dumps(data, indent=2) + "\n")


def build_wheel(source: Path, destination: Path) -> str:
    """Build SOURCE's wheel into DESTINATION; return its SHA-256."""
    run(
        ["uv", "build", "--wheel", "--out-dir", str(destination), str(source)],
        os.environ,
    )
    (wheel,) = destination.glob("meow_harness-*.whl")
    return hashlib.sha256(wheel.read_bytes()).hexdigest()


def run(argv: list[str], env: dict, **kwargs) -> str:
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=900, **kwargs
    )
    if result.returncode:
        raise RuntimeError(f"{argv}:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def bootstrap(env: dict) -> None:
    if os.name == "nt":
        script = str(ROOT / "scripts/install.ps1").replace("'", "''")
        argv = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Invoke-Expression ([IO.File]::ReadAllText('{script}')); "
            "exit $LASTEXITCODE",
        ]
        run(argv, env, input="n\nn\n")
    else:
        run(["sh"], env, input=(ROOT / "scripts/install.sh").read_text())


def check_install(home: Path, env: dict, version: str) -> None:
    executable = home / ".meow/bin" / ("meow.exe" if os.name == "nt" else "meow")
    assert run([str(executable), "--version"], env).strip() == f"meow {version}"
    assert "--update" in run([str(executable), "--help"], env)
    assert "--no-worktree-commit" in run([str(executable), "run", "--help"], env)
    alias = executable.with_name("mw.exe" if os.name == "nt" else "mw")
    assert run([str(alias), "--version"], env).strip() == f"meow {version}"
    release = home / ".meow/versions" / version
    python = release_python(release)
    probe = (
        "import subprocess,sys; import meow.agents.base; import meow.ui.server; "
        "import IPython; from meow.infrastructure.scheduling import _run_argv; "
        "from meow.ui.sessions import _builtin_skill_names; "
        "assert 'run' in _builtin_skill_names(); "
        "subprocess.run([sys.executable,'-m','meow','run','--help'],check=True)"
    )
    run([str(python), "-c", probe], env)
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"]["meow@meow"] is True
    assert (release / "plugin/skills/run/SKILL.md").is_file()


def smoke() -> None:  # ruff: ignore[too-many-statements] -- end-to-end scenario
    released = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    major, minor, patch = released.split(".")
    version = f"{major}.{minor}.{int(patch) + SMOKE_PATCH_OFFSET}"
    # pip splits PIP_FIND_LINKS on whitespace, so wheels live in a plain path.
    with (
        tempfile.TemporaryDirectory(prefix="meow-wheels-") as wheel_directory,
        tempfile.TemporaryDirectory(prefix="meow release smoke ") as directory,
    ):
        temporary = Path(directory)
        home = temporary / "user with spaces"
        home.mkdir()
        # pip finds the unpublished wheel here, standing in for the index.
        wheels = Path(wheel_directory)
        source = temporary / "source"
        source.mkdir()
        snapshot(source, version)
        digest = build_wheel(source, wheels)
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            functools.partial(ReleaseServer, directory=str(temporary / "server")),
        )
        server.tag = version
        server.digest = digest
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # Test-only startup patch: never modify the developer's real Windows
        # registry PATH or contact GitHub while exercising a local fixture.
        startup = temporary / "startup"
        startup.mkdir()
        # Python processes before MEOW is installed cannot import the patch.
        (startup / "sitecustomize.py").write_text(
            "try:\n import meow_patch\nexcept ImportError:\n pass\n"
        )
        (startup / "meow_patch.py").write_text(
            "import os\n"
            "from meow.installer import releases, maintenance\n"
            "releases._add_to_path = lambda path: None\n"
            # `python -m ...releases` executes as __main__, so patching its
            # imported copy above alone cannot intercept that activation.
            "if os.name == 'nt':\n"
            " import winreg\n"
            " original_set = winreg.SetValueEx\n"
            " def safe_set(key, name, *args):\n"
            "  if name != 'Path': return original_set(key, name, *args)\n"
            " winreg.SetValueEx = safe_set\n"
            "maintenance.check_for_updates = lambda: None\n"
            "maintenance.RELEASE_URL = os.environ['MEOW_RELEASE_MANIFEST_URL']\n"
        )
        base = f"http://127.0.0.1:{server.server_port}"
        env = {
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONPATH": str(startup),
            "MEOW_PYTHON": sys.executable,
            "PIP_FIND_LINKS": str(wheels),
            "MEOW_RELEASE_MANIFEST_URL": base + "/latest.json",
        }
        try:
            bootstrap(env)
            check_install(home, env, version)
            check_launcher_arguments(home, env, temporary)
            config = home / ".meow/config.toml"
            config.write_text("# preserved user configuration\n")
            bootstrap(env)
            assert config.read_text() == "# preserved user configuration\n"
            check_install(home, env, version)
            check_other_python(home, env, version)
            check_update(home, env, version)
            assert (home / ".meow/current-version").read_text().strip() == version
        finally:
            server.shutdown()
            server.server_close()
    print(
        "Release smoke passed: streamed install, CLI, alias, plugin, child "
        "Python, reinstall, Python change, and update."
    )


def check_launcher_arguments(home: Path, env: dict, temporary: Path) -> None:
    probe = temporary / "argv probe"
    package = probe / "meow"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(
        "import json,sys; print(json.dumps(sys.argv[1:])); "
        "sys.exit(7 if sys.argv[1:]==['exit7'] else 0)"
    )
    executable = home / ".meow/bin" / ("meow.exe" if os.name == "nt" else "meow")
    argv = [
        "spaces and unicode 猫",
        'quote"here',
        "",
        "trailing\\",
        "& echo unsafe",
        "%PATH%",
    ]
    test_env = {**env, "PYTHONPATH": str(probe)}
    assert json.loads(run([str(executable), *argv], test_env)) == argv
    result = subprocess.run(
        [str(executable), "exit7"], env=test_env, capture_output=True, timeout=30
    )
    assert result.returncode == PROBE_EXIT_CODE


def check_other_python(home: Path, env: dict, version: str) -> None:
    """An install made by another Python is rebuilt by the installer."""
    config = home / ".meow/versions" / version / "runtime/pyvenv.cfg"
    config.write_text(config.read_text().replace("version = ", "version = 0.", 1))
    bootstrap(env)
    assert "version = 0." not in config.read_text()
    check_install(home, env, version)


def check_update(home: Path, env: dict, version: str) -> None:
    """Make the installed runtime older, then update through the real launcher."""
    versions = home / ".meow/versions"
    previous = versions / "0.0.0"
    (versions / version).rename(previous)
    metadata = next((previous / "runtime").rglob("meow_harness-*.dist-info/METADATA"))
    metadata.write_text(
        metadata.read_text().replace(f"Version: {version}", "Version: 0.0.0")
    )
    (home / ".meow/current-version").write_text("0.0.0\n")
    executable = home / ".meow/bin" / ("meow.exe" if os.name == "nt" else "meow")
    assert run([str(executable), "--version"], env).strip() == "meow 0.0.0"
    run([str(executable), "-u"], env)
    check_install(home, env, version)
    assert previous.is_dir()
    assert (
        home / ".meow/config.toml"
    ).read_text() == "# preserved user configuration\n"


def annotate_failure(error: BaseException) -> None:
    """Surface the failure as a GitHub annotation; job logs need a login."""
    text = f"{type(error).__name__}: {error}"[-3000:]
    print("::error title=Release smoke failed::" + text.replace(chr(10), "%0A"))


if __name__ == "__main__":
    try:
        smoke()
    except Exception as error:
        annotate_failure(error)
        raise

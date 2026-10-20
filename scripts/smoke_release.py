"""Exercise a real bundle through a local release server, without a checkout install."""

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_EXIT_CODE = 7
sys.path.insert(0, str(ROOT / "src"))

from meow.installer.releases import asset_name  # ruff: ignore[module-import-not-at-top-of-file]


class ReleaseServer(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/latest":
            self.send_response(302)
            self.send_header("Location", f"/tag/{self.server.tag}")
            self.end_headers()
        elif self.path == "/latest.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"tag_name": self.server.tag}, indent=2).encode()
            )
        elif self.path.startswith("/tag/"):
            self.send_response(200)
            self.end_headers()
        else:
            super().do_GET()


def run(argv: list[str], env: dict, **kwargs) -> str:
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=180, **kwargs
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
    assert "--auto" in run([str(executable), "run", "--help"], env)
    alias = executable.with_name("mw.exe" if os.name == "nt" else "mw")
    assert run([str(alias), "--version"], env).strip() == f"meow {version}"
    release = home / ".meow/versions" / version
    python = release / "runtime" / ("python.exe" if os.name == "nt" else "bin/python3")
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
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="meow release smoke ") as directory:
        temporary = Path(directory)
        home = temporary / "user with spaces"
        home.mkdir()
        fixtures = temporary / "server/download" / version
        fixtures.mkdir(parents=True)
        archive = ROOT / "dist" / asset_name()
        shutil.copy2(archive, fixtures)
        shutil.copy2(archive.with_name(archive.name + ".txt"), fixtures)
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            functools.partial(ReleaseServer, directory=str(temporary / "server")),
        )
        server.tag = version
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # Test-only startup patch: never modify the developer's real Windows
        # registry PATH or contact GitHub while exercising a local fixture.
        startup = temporary / "startup"
        startup.mkdir()
        (startup / "sitecustomize.py").write_text(
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
            "releases.DOWNLOAD_URL = "
            "os.environ['MEOW_RELEASE_BASE_URL'] + '/download'\n"
        )
        base = f"http://127.0.0.1:{server.server_port}"
        env = {
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONPATH": str(startup),
            "MEOW_RELEASE_BASE_URL": base,
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
            check_update(home, env, version)
            assert (home / ".meow/current-version").read_text().strip() == version
        finally:
            server.shutdown()
            server.server_close()
    print(
        "Release smoke passed: streamed install, relocation, CLI, alias, "
        "plugin, child Python, reinstall, and update."
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


def check_update(home: Path, env: dict, version: str) -> None:
    """Make the installed runtime older, then update through the real launcher."""
    versions = home / ".meow/versions"
    previous = versions / "0.0.0"
    (versions / version).rename(previous)
    metadata = next((previous / "runtime").rglob("meow-*.dist-info/METADATA"))
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


if __name__ == "__main__":
    smoke()

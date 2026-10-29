"""Install MEOW wheels from the package index into ~/.meow/versions."""

import argparse
import contextlib
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from meow.common.atomic import move_directory, replace_with_retry
from meow.installer import maintenance
from meow.project.claude_settings import register_checkout_plugin

DISTRIBUTION = "meow-harness"
RELEASE_DATA = Path(__file__).resolve().parents[1] / "_release"
MINIMUM_PYTHON = (3, 12)
# No quotes: Windows PowerShell 5.1 strips them from native arguments.
PROBE = (
    "import os,sys;v=sys.version_info;"
    "print(v[0],v[1],v[2],os.path.realpath(sys._base_executable))"
)
PROBE_FIELDS = 4
PYTHON_NAME = re.compile(r"python(3(\.\d+)?)?")


@contextlib.contextmanager
def progress(message: str):
    """Show MESSAGE with a spinner on a terminal, or plainly when piped."""
    if not sys.stdout.isatty():
        print(f"{message}...", flush=True)
        yield
        return
    done = threading.Event()

    def spin() -> None:
        for frame in itertools.cycle("|/-\\"):
            print(f"\r{message}... {frame}", end="", flush=True)
            if done.wait(0.15):
                return

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()
        print(f"\r{message}... done\033[K", flush=True)


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _lines(*argv: str) -> list[str]:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, errors="replace", timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return result.stdout.splitlines() if result.returncode == 0 else []


def _windows_candidates() -> list[str]:
    found = _lines("where.exe", "python")
    for line in _lines("py", "-0p"):
        match = re.search(r"[A-Za-z]:\\.*\.exe\s*$", line)
        if match:
            found.append(match.group().strip())
    return found


def _path_candidates() -> list[str]:
    found = []
    for directory in filter(None, os.environ.get("PATH", "").split(os.pathsep)):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        found += [
            os.path.join(directory, name)
            for name in names
            if PYTHON_NAME.fullmatch(name)
        ]
    return found


def _probe(candidate: str) -> tuple[tuple[int, ...], Path] | None:
    try:
        result = subprocess.run(
            [candidate, "-c", PROBE], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = result.stdout.strip().split(" ", PROBE_FIELDS - 1)
    if (
        result.returncode
        or len(fields) != PROBE_FIELDS
        or not all(f.isdecimal() for f in fields[:3])
    ):
        return None
    return tuple(map(int, fields[:3])), Path(fields[3])


def find_python() -> Path:
    """Return the newest Python 3.12+ on PATH, or the one MEOW_PYTHON names.

    Windows asks `where python` (plus the `py` launcher); elsewhere PATH is
    scanned. Virtual environments resolve to their base interpreter.
    """
    override = os.environ.get("MEOW_PYTHON")
    if override:
        candidates = [override]
    else:
        candidates = _windows_candidates() if os.name == "nt" else _path_candidates()
    found = [
        probe
        for probe in map(_probe, candidates)
        if probe and probe[0][:2] >= MINIMUM_PYTHON
    ]
    if not found:
        raise RuntimeError(
            "MEOW needs Python 3.12 or newer. Install it (winget install "
            "Python.Python.3.13, brew install python, or apt install python3), "
            "or set MEOW_PYTHON to its path."
        )
    return max(found, key=lambda item: item[0])[1]


def release_python(release: Path) -> Path:
    runtime = release / "runtime"
    return runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")


def activate(release: Path) -> None:
    """Register the plugin, then atomically select this immutable release."""
    release = release.resolve()
    maintenance._version_parts(release.name)
    root = release.parent.parent
    if release.parent.name != "versions":
        raise ValueError("MEOW releases must be installed under .meow/versions")
    executable = "meow.exe" if os.name == "nt" else "meow"
    source = release / "bin" / executable
    if not source.is_file():
        raise ValueError("MEOW release launcher is missing")
    register_checkout_plugin(settings_path(), release / "plugin")
    print(
        f"Registered and enabled MEOW's Claude Code plugin in {settings_path()}\n"
        f"Plugin directory: {release / 'plugin'}\n"
        "Restart Claude Code to load /meow:* skills (including /meow:run)."
    )
    _install_launcher(source, root / "bin")
    _add_to_path(root / "bin")
    print(
        f"Installed meow and mw commands in {root / 'bin'} and added it to PATH.\n"
        "Open a new terminal to use them."
    )
    pointer = root / "current-version"
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as stream:
        stream.write(release.name + "\n")
    replace_with_retry(Path(stream.name), pointer)
    print(f"Activated MEOW {release.name}")


def _install_launcher(source: Path, bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for stale in bin_dir.glob("*.old-*"):
        with contextlib.suppress(OSError):  # still running; retried next time
            stale.unlink()
    for name in (source.name, "mw.exe" if os.name == "nt" else "mw"):
        target = bin_dir / name
        temporary = target.with_suffix(".new")
        shutil.copy2(source, temporary)
        if os.name == "nt" and target.exists():
            # Windows cannot overwrite a running launcher (an update runs under
            # the old one) but can rename it, and older launchers expect a
            # different runtime layout, so they must always be replaced.
            replace_with_retry(
                target, target.with_name(f"{name}.old-{uuid.uuid4().hex[:8]}")
            )
        replace_with_retry(temporary, target)


def _add_to_path(bin_dir: Path) -> None:
    if os.name == "nt":
        _windows_path(bin_dir)
    else:
        line = '\nexport PATH="$HOME/.meow/bin:$PATH"\n'
        profiles = [Path.home() / ".profile", Path.home() / ".bashrc"]
        if (Path.home() / ".bash_profile").exists():
            profiles.append(Path.home() / ".bash_profile")
        if Path(os.environ.get("SHELL", "")).name == "zsh":
            profiles.append(Path.home() / ".zshrc")
        for profile in profiles:
            content = profile.read_text() if profile.exists() else ""
            if line.strip() not in content.splitlines():
                with profile.open("a") as stream:
                    stream.write(line)
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _windows_path(bin_dir: Path) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in current.split(";") if entry]
        wanted = os.path.normcase(str(bin_dir))
        entries = [
            entry
            for entry in entries
            if os.path.normcase(os.path.expandvars(entry)) != wanted
        ]
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([str(bin_dir), *entries]))
    _notify_environment()


def _notify_environment() -> None:
    import ctypes

    # Explorer and newly opened terminals must refresh the per-user PATH.
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 2, 1000, None
    )


def _run(*argv: str, **kwargs) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def _pip(python: Path, *args: str) -> None:
    if shutil.which("uv"):
        try:
            _run("uv", "pip", "install", "--quiet", "--python", str(python), *args)
            return
        except (OSError, RuntimeError):
            pass  # uv is optional; pip below does the work
    command = (str(python), "-m", "pip", "install", "--disable-pip-version-check")
    _run(*command, "--no-input", *args)


def _venv(python: Path, runtime: Path) -> None:
    """Create RUNTIME with uv when it is installed, otherwise with venv."""
    if shutil.which("uv"):
        try:
            args = ("venv", "--quiet", "--seed", "--python", str(python), str(runtime))
            _run("uv", *args)
            return
        except (OSError, RuntimeError):
            shutil.rmtree(runtime, ignore_errors=True)  # uv is optional
    _run(str(python), "-m", "venv", str(runtime))


def stage_bundle(python: Path, bundle: Path, tag: str, digest: str) -> None:
    """Create BUNDLE/runtime and install exactly the published wheel into it."""
    try:
        _venv(python, bundle / "runtime")
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc}\nThis Python could not create a virtual environment. "
            "On Debian/Ubuntu install the python3-venv package."
        ) from exc
    requirement = bundle.parent / "wheel.txt"
    requirement.write_text(f"{DISTRIBUTION}=={tag} --hash=sha256:{digest}\n")
    options = ("--no-deps", "--require-hashes", "--only-binary", ":all:")
    _pip(release_python(bundle), *options, "-r", str(requirement))


def _build_launcher(bundle: Path) -> None:
    target = bundle / "bin"
    target.mkdir()
    source = RELEASE_DATA / "launcher"
    if os.name == "nt":
        compiler = (
            Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        )
        _run(
            str(compiler),
            "/nologo",
            "/optimize+",
            "/target:exe",
            f"/out:{target / 'meow.exe'}",
            str(source / "Launcher.cs"),
        )
    else:
        shutil.copy2(source / "launcher.sh", target / "meow")
        (target / "meow").chmod(0o755)


def _swap(bundle: Path, destination: Path) -> None:
    """Move BUNDLE into place, setting any previous build aside until it works."""
    if not destination.exists():
        move_directory(bundle, destination)
        return
    retired = destination.with_name(f".old-{destination.name}-{os.getpid()}")
    try:
        replace_with_retry(destination, retired)
    except OSError as exc:
        raise RuntimeError(
            f"{destination} is in use. Stop running MEOW jobs, then rerun."
        ) from exc
    try:
        move_directory(bundle, destination)
    except OSError:
        replace_with_retry(retired, destination)
        raise
    shutil.rmtree(retired, ignore_errors=True)


def finish_bundle(bundle: Path, destination: Path) -> None:
    """Complete a staged bundle: pinned dependencies, launcher, plugin, swap.

    Runs from the new wheel (on PYTHONPATH of any Python 3.12+), never from
    inside BUNDLE, so Windows can rename the folder afterwards. The venv is not
    relocatable, but launchers use `python -m meow`, which survives the move.
    """
    lock = RELEASE_DATA / "requirements-release.txt"
    print("Installing pinned dependencies (this can take a minute)...", flush=True)
    _pip(release_python(bundle), "--require-hashes", "-r", str(lock))
    print("Building the meow launcher...", flush=True)
    _build_launcher(bundle)
    print("Installing the Claude Code plugin...", flush=True)
    shutil.copytree(RELEASE_DATA / "plugin", bundle / "plugin")
    shutil.copy2(RELEASE_DATA / "LICENSE", bundle / "LICENSE")
    _swap(bundle, destination)


def install_release(tag: str, digest: str) -> Path:
    """Install TAG's wheel with the newest Python, activate it, return its path."""
    maintenance._version_parts(tag)
    with progress("Locating Python 3.12+"):
        python = find_python()
    print(f"Using {python}", flush=True)
    versions = Path.home() / ".meow" / "versions"
    destination = versions / tag
    versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=versions, prefix=".install-") as temporary:
        bundle = Path(temporary) / "bundle"
        bundle.mkdir()
        with progress(f"Creating environment and installing MEOW {tag}"):
            stage_bundle(python, bundle, tag, digest)
        purelib = _run(
            str(release_python(bundle)),
            "-c",
            "import sysconfig;print(sysconfig.get_path('purelib'))",
        ).strip()
        # The new release finishes its own install, whatever this one does.
        env = {**os.environ, "PYTHONPATH": purelib}
        with progress("Installing pinned dependencies and launcher (can take minutes)"):
            _run(
                str(python),
                "-m",
                "meow.installer.releases",
                "--finish",
                str(destination),
                "--bundle",
                str(bundle),
                env=env,
            )
    with progress("Verifying and activating the release"):
        _validate_release(destination, tag)
        _run(
            str(release_python(destination)),
            "-m",
            "meow.installer.releases",
            "--activate",
            str(destination),
        )
    return destination


def _validate_release(release: Path, tag: str) -> None:
    result = subprocess.run(
        [
            str(release_python(release)),
            "-c",
            "from importlib.metadata import version; print(version('meow-harness'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != tag:
        raise ValueError("MEOW release package version does not match its tag")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", type=Path)
    parser.add_argument("--finish", type=Path, help="destination for --bundle")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--mcp", help="providers to register at Claude user scope")
    parser.add_argument("--bun", action="store_true")
    parser.add_argument("--install-missing", action="store_true")
    args = parser.parse_args()
    if args.finish:
        if not args.bundle:
            parser.error("--finish requires --bundle")
        finish_bundle(args.bundle, args.finish)
        return 0
    if not args.activate:
        parser.error("--activate or --finish is required")
    _validate_release(args.activate, args.activate.name)
    activate(args.activate)
    print(f"Installed MEOW {args.activate.name} in {args.activate.parent.parent}")
    return _optional_setup(args)


def _optional_setup(args: argparse.Namespace) -> int:
    from meow.installer._runtime import _optional_setup as run

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Install portable release bundles under the user's ~/.meow directory."""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sysconfig
import tarfile
import tempfile
import zipfile
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError

from meow.installer import maintenance
from meow.project.claude_settings import register_checkout_plugin

DOWNLOAD_URL = "https://github.com/maya8237/MEOW/releases/download"


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def asset_name() -> str:
    systems = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}
    machines = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        system = systems[platform.system()]
        architecture = (
            sysconfig.get_platform().rsplit("-", 1)[-1]
            if system == "windows"
            else platform.machine()
        )
        machine = machines[architecture.lower()]
    except KeyError as exc:
        raise RuntimeError("No MEOW release is available for this platform") from exc
    extension = "zip" if system == "windows" else "tar.gz"
    return f"meow-{system}-{machine}.{extension}"


def unpack_release(archive: Path, digest: str | None, destination: Path) -> None:
    if digest is not None:
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise ValueError(
                "MEOW release checksum mismatch; installation was not changed"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary) / "release"
        staging.mkdir()
        _extract(archive, staging)
        staging.rename(destination)


def _extract(archive: Path, staging: Path) -> None:
    if not zipfile.is_zipfile(archive):
        with tarfile.open(archive) as bundle:
            bundle.extractall(staging, filter="data")
        return
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (staging / member.filename).resolve()
            if not target.is_relative_to(staging.resolve()):
                raise ValueError("Unsafe path in MEOW release archive")
        bundle.extractall(staging)


def release_python(release: Path) -> Path:
    return release / "runtime" / ("python.exe" if os.name == "nt" else "bin/python3")


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
    _install_launcher(source, root / "bin")
    _add_to_path(root / "bin")
    pointer = root / "current-version"
    with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as stream:
        stream.write(release.name + "\n")
    Path(stream.name).replace(pointer)


def _install_launcher(source: Path, bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in (source.name, "mw.exe" if os.name == "nt" else "mw"):
        target = bin_dir / name
        # Windows locks the running launcher. It only reads current-version;
        # changing that pointer updates the application without replacing it.
        if os.name != "nt" or not target.exists():
            temporary = target.with_suffix(".new")
            shutil.copy2(source, temporary)
            temporary.replace(target)


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


def _download(url: str, destination: Path) -> None:
    with (
        maintenance.urlopen(url, timeout=120) as response,
        destination.open("wb") as stream,
    ):
        shutil.copyfileobj(response, stream)


def _fetch_digest(tag: str, asset: str, url: str, checksum: Path) -> str | None:
    try:
        _download(url, checksum)
    except HTTPError as exc:
        if exc.code != HTTPStatus.NOT_FOUND:
            raise
        # Temporary: revert this exception when release checksums are available.
        print(
            f"Release {tag} is missing its checksum file: {asset}.sha256; "
            "temporarily continuing without checksum verification."
        )
        return None
    return checksum.read_text().split()[0]


def install_release(tag: str) -> None:
    maintenance._version_parts(tag)
    root = Path.home() / ".meow"
    destination = root / "versions" / tag
    asset = asset_name()
    if not destination.exists():
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            archive = Path(temporary) / asset
            checksum = Path(temporary) / "checksum"
            base = f"{DOWNLOAD_URL}/{tag}/{asset}"
            _download(base, archive)
            digest = _fetch_digest(tag, asset, base + ".sha256", checksum)
            unpack_release(archive, digest, destination)
    _validate_release(destination, tag)
    subprocess.run(
        [
            str(release_python(destination)),
            "-m",
            "meow.installer.releases",
            "--activate",
            str(destination),
        ],
        check=True,
    )


def _validate_release(release: Path, tag: str) -> None:
    result = subprocess.run(
        [
            str(release_python(release)),
            "-c",
            "from importlib.metadata import version; print(version('meow'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != tag:
        raise ValueError("MEOW release package version does not match its tag")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", type=Path, required=True)
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()
    _validate_release(args.activate, args.activate.name)
    activate(args.activate)
    print(f"Installed MEOW {args.activate.name} in {args.activate.parent.parent}")
    if args.setup:
        from meow.installer._runtime import _setup_projects

        return _setup_projects()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

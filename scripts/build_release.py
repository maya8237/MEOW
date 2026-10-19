"""Build a relocatable MEOW runtime and plugin bundle on the target platform."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meow.installer.releases import asset_name, release_python  # ruff: ignore[module-import-not-at-top-of-file]


def run(*argv: str, **kwargs) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def build_launcher(bundle: Path) -> None:
    target = bundle / "bin"
    target.mkdir()
    if os.name == "nt":
        compiler = (
            Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        )
        run(
            str(compiler),
            "/nologo",
            "/optimize+",
            "/target:exe",
            f"/out:{target / 'meow.exe'}",
            str(ROOT / "scripts/release/Launcher.cs"),
        )
    else:
        shutil.copy2(ROOT / "scripts/release/launcher.sh", target / "meow")
        (target / "meow").chmod(0o755)


def build_runtime(bundle: Path, temporary: Path) -> None:
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(temporary / "python")}
    run(
        "uv",
        "python",
        "install",
        "3.12",
        "--no-progress",
        "--no-bin",
        *(["--no-registry"] if os.name == "nt" else []),
        env=env,
    )
    python = Path(
        run(
            "uv",
            "python",
            "find",
            "--managed-python",
            "3.12",
            env=env,
        ).strip()
    )
    runtime = python.parent if os.name == "nt" else python.parent.parent
    shutil.copytree(runtime, bundle / "runtime", symlinks=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    wheel = ROOT / "dist" / f"meow-{version}-py3-none-any.whl"
    run(
        "uv",
        "pip",
        "install",
        "--system",
        "--break-system-packages",
        "--python",
        str(release_python(bundle)),
        "--no-deps",
        str(wheel),
    )
    run(
        "uv",
        "pip",
        "install",
        "--system",
        "--break-system-packages",
        "--python",
        str(release_python(bundle)),
        "--require-hashes",
        "-r",
        str(ROOT / "requirements-release.txt"),
    )


def check_version() -> str:
    project = ROOT / "pyproject.toml"
    version = tomllib.loads(project.read_text())["project"]["version"]
    tag = os.environ.get("GITHUB_REF_NAME")
    if tag:
        if not re.fullmatch(
            r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag
        ):
            raise ValueError(f"Expected a three-part release tag, got {tag!r}")
        version = tag.removeprefix("v")
        project.write_text(
            re.sub(
                r"(?m)^version = .*$",
                f'version = "{version}"',
                project.read_text(),
                count=1,
            )
        )
    for manifest in (ROOT / ".claude-plugin").glob("*.json"):
        data = json.loads(manifest.read_text())
        target = data if "version" in data else data["plugins"][0]
        if tag:
            target["version"] = version
            manifest.write_text(json.dumps(data, indent=2) + "\n")
        elif target["version"] != version:
            raise ValueError(f"{manifest} does not match package version {version}")
    return version


def build() -> Path:
    check_version()
    run("uv", "build", "--wheel", "--out-dir", str(ROOT / "dist"), str(ROOT))
    output = ROOT / "dist" / asset_name()
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        bundle = temporary / "bundle"
        bundle.mkdir()
        build_runtime(bundle, temporary)
        build_launcher(bundle)
        plugin = bundle / "plugin"
        plugin.mkdir()
        for folder in ("skills", ".claude-plugin", "hooks"):
            if (ROOT / folder).is_dir():
                shutil.copytree(ROOT / folder, plugin / folder)
        shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
        suffix = ".zip" if os.name == "nt" else ".tar.gz"
        shutil.make_archive(
            str(output).removesuffix(suffix),
            "zip" if os.name == "nt" else "gztar",
            bundle,
        )
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.with_name(output.name + ".sha256").write_text(f"{digest}  {output.name}\n")
    return output


if __name__ == "__main__":
    print(build())

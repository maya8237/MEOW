"""Stamp a release tag into the package and plugin versions before building.

With no tag, only check that the package and plugin manifests agree.
"""

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def check_version(tag: str | None = None) -> str:
    project = ROOT / "pyproject.toml"
    version = tomllib.loads(project.read_text())["project"]["version"]
    if tag:
        if not TAG.fullmatch(tag):
            raise ValueError(f"Expected a three-part release tag, got {tag!r}")
        version = tag
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    print(check_version(parser.parse_args().tag))


if __name__ == "__main__":
    main()

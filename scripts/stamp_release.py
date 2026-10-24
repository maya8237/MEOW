"""Stamp a release tag into the package and plugin versions before building.

The tag also replaces "unreleased" in the onboarding announcement table; an
announcement for a later version fails. With no tag, only check that the
package and plugin manifests agree.
"""

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ANNOUNCEMENTS = Path("src/meow/project/announcements.py")
UNRELEASED = "unreleased"


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


def _parts(version: str) -> tuple[int, ...]:
    if not TAG.fullmatch(version):
        raise ValueError(f"Expected a three-part release version, got {version!r}")
    return tuple(int(part) for part in version.split("."))


def _since_nodes(source: str) -> list[tuple[str, ast.Constant]]:
    """Each announced key with the node holding its release version."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "ANNOUNCED"
        ):
            return [
                (key.value, value.elts[0])
                for key, value in zip(node.value.keys, node.value.values, strict=True)
            ]
    raise ValueError(f"{ANNOUNCEMENTS} has no ANNOUNCED table")


def unreleased() -> list[str]:
    source = (ROOT / ANNOUNCEMENTS).read_text()
    return [key for key, node in _since_nodes(source) if node.value == UNRELEASED]


def stamp_announcements(tag: str, keys: set[str] | None = None) -> list[str]:
    """Stamp TAG on unreleased entries (only KEYS when given); return them."""
    path = ROOT / ANNOUNCEMENTS
    lines = path.read_text().splitlines(keepends=True)
    stamped = []
    for key, node in reversed(_since_nodes("".join(lines))):
        if node.value != UNRELEASED:
            if _parts(node.value) > _parts(tag):
                raise ValueError(f"{key} is announced for {node.value}, after {tag}")
        elif keys is None or key in keys:
            line = lines[node.lineno - 1]
            lines[node.lineno - 1] = (
                line[: node.col_offset] + json.dumps(tag) + line[node.end_col_offset :]
            )
            stamped.append(key)
    path.write_text("".join(lines))
    return sorted(stamped)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    parser.add_argument(
        "--keys", help="comma-separated announcements to stamp (default: all)"
    )
    parser.add_argument(
        "--unreleased", action="store_true", help="list unreleased announcements"
    )
    args = parser.parse_args()
    if args.unreleased:
        print(",".join(unreleased()))
        return
    version = check_version(args.tag)
    if args.tag:
        keys = None if args.keys is None else set(filter(None, args.keys.split(",")))
        stamp_announcements(args.tag, keys)
    print(version)


if __name__ == "__main__":
    main()

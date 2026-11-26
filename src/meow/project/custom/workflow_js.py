"""Workflows as Claude Code workflow scripts that MEOW's engine also reads.

A workflow file is `<name>.js`, a Claude Code dynamic workflow:

    export const meta = {"name": "...", "description": "..."}

    const NAME = "..."  // meta is not in scope while the script runs
    const MEOW = /*meow:workflow*/ {"steps": [...]} /*meow:end*/

    // ---- MEOW runtime ... (generated)

`meta` and the block between the markers are JSON (also valid JavaScript), so
MEOW reads them without running anything. The block holds what a TOML workflow
held besides `name` and `description`: `steps`, and optionally `auto_test`,
`short_plan`, and `ejected_from`. `meow run` executes the steps with MEOW's
engine; in Claude Code the appended runtime executes the same steps through the
`meow native` helpers.
"""

import hashlib
import json
import re
import tomllib
from functools import cache
from pathlib import Path

BEGIN, END = "/*meow:workflow*/", "/*meow:end*/"
_META = re.compile(r"\A\s*export const meta = (\{[^\n]*\})\s*\n")
_NAME = re.compile(r'^const NAME = ("[^"\n]*")', re.MULTILINE)
_BLOCK = re.compile(re.escape(BEGIN) + r"(.*?)" + re.escape(END), re.DOTALL)
_BLOCK_KEYS = frozenset({"steps", "auto_test", "short_plan", "ejected_from"})


@cache
def runtime() -> str:
    return (Path(__file__).with_name("workflow_runtime.js")).read_text(encoding="utf-8")


def _parts(path: Path, text: str) -> tuple[object, object]:
    """The parsed `meta` and definition block of a workflow script."""
    meta, block = _META.match(text), _BLOCK.search(text)
    if meta is None:
        raise ValueError(
            f"{path}: must start with `export const meta = {{...}}` on one line"
        )
    if block is None:
        raise ValueError(f"{path}: missing the {BEGIN} ... {END} definition")
    try:
        return json.loads(meta[1]), json.loads(block[1])
    except ValueError as exc:
        raise ValueError(
            f"{path}: meta and the definition must be JSON: {exc}"
        ) from exc


def read_workflow_js(path: Path) -> dict:
    """The workflow's data, keyed like a TOML workflow's top-level table."""
    text = path.read_bytes().decode("utf-8-sig")
    header, data = _parts(path, text)
    if not isinstance(header, dict) or set(header) - {"name", "description"}:
        raise ValueError(f"{path}: meta holds only name and description")
    named = _NAME.search(text)
    if named is None or json.loads(named[1]) != header.get("name"):
        raise ValueError(f"{path}: `const NAME = ...` must repeat meta's name")
    if not isinstance(data, dict) or set(data) - _BLOCK_KEYS:
        raise ValueError(f"{path}: the definition holds only {sorted(_BLOCK_KEYS)}")
    return {**header, **data}


def to_js(name: str, data: dict) -> str:
    """The `.js` file for workflow `name` with TOML-shaped `data`."""
    unknown = set(data) - _BLOCK_KEYS - {"name", "description"}
    if unknown:
        raise ValueError(f"workflow {name!r}: unsupported keys {sorted(unknown)}")
    meta = {"name": name, "description": data["description"]}
    block = {
        key: data[key]
        for key in ("ejected_from", "auto_test", "short_plan")
        if key in data
    }
    block["steps"] = data.get("steps", [])
    return (
        f"export const meta = {json.dumps(meta, ensure_ascii=False)}\n\n"
        "// The MEOW workflow definition: edit the JSON between the markers.\n"
        f"// `meow run --workflow {name}` runs it with MEOW's engine; in Claude Code\n"
        "// the runtime below runs it as a workflow command.\n"
        f"const NAME = {json.dumps(name)}  // meta is not in scope while it runs\n"
        f"const MEOW = {BEGIN} {json.dumps(block, indent=2, ensure_ascii=False)}"
        f" {END}\n\n" + runtime()
    )


_EJECT_HEADER = re.compile(
    rb'\Aejected_from = "builtin:([a-z0-9]+(?:-[a-z0-9]+)*)@([0-9a-f]{64})"\r?\n\r?\n'
)


def _shipped_data(name: str) -> tuple[dict, str] | None:
    from meow.project.custom.sources import builtin_dir

    path = builtin_dir() / "workflows" / f"{name}.js"
    if not path.is_file():
        return None
    return read_workflow_js(path), hashlib.sha256(path.read_bytes()).hexdigest()


def _migrated(path: Path) -> tuple[str, str]:
    """The `.js` text for TOML workflow `path`, and a note on what changed.

    A byte-for-byte eject nobody edited is brought up to the shipped version;
    an unchanged definition is restamped so the format change is not drift.
    """
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    header = _EJECT_HEADER.match(raw)
    shipped = _shipped_data(header[1].decode()) if header else None
    if header and shipped:
        current, digest = shipped
        origin = f"builtin:{header[1].decode()}@{digest}"
        untouched = (
            hashlib.sha256(raw[header.end() :]).hexdigest() == header[2].decode()
        )
        if untouched:
            current = {**current, "ejected_from": origin}
            note = " (never edited: updated to the shipped version)"
            return to_js(path.stem, current), note
        same = {k: v for k, v in data.items() if k not in {"ejected_from", "name"}}
        if same == {k: v for k, v in current.items() if k != "name"}:
            data = {**data, "ejected_from": origin}
    data.pop("name", None)
    return to_js(path.stem, data), ""


def migrate_toml_workflows(directories: list[Path], apply: bool = True) -> list[str]:
    """Rewrite each retired `.toml` workflow in `directories` as `.js`.

    The script is written before the TOML is removed; a file that cannot be
    read or converted, or whose `.js` name is taken, is reported and left.
    """
    done = []
    verb = "migrated" if apply else "will migrate"
    for directory in dict.fromkeys(Path(d) for d in directories):
        for path in sorted(directory.glob("*.toml")) if directory.is_dir() else []:
            target = path.with_suffix(".js")
            if target.exists():
                done.append(f"skipped {path}: {target.name} already exists")
                continue
            try:
                text, note = _migrated(path)
            except (OSError, ValueError, KeyError, UnicodeError) as exc:
                done.append(f"skipped {path}: {exc}")
                continue
            done.append(f"{verb} {path} -> {target.name}{note}")
            if apply:
                target.write_text(text, encoding="utf-8", newline="\n")
                path.unlink()
    return done

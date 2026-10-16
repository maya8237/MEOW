"""Code-owned whole-project review inventory and grounded coverage contract."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from meow.project.config_files import SHARED_PROJECT_PATHS
from meow.project.plan_files import is_review_report

_EXCLUDED_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    ".cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".test-tmp",
    "dist",
    "build",
    "coverage",
    "htmlcov",
    "target",
    "worktrees",
    ".worktrees",
})
_PRIVATE_NAMES = frozenset({
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
})
_PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
_GENERATED_SUFFIXES = {".pyc", ".pyo", ".map", ".lock"}
_CONTAINERS = {"src", "apps", "packages", "services", ".meow"}
_AREA_DEPTH = 2
_WHOLE_PROJECT = re.compile(
    r"\b(?:whole|entire|full)\s+(?:repo(?:sitory)?|project|codebase|code\s*base)\b"
    r"|\b(?:all\s+(?:the\s+)?(?:code|files)|everything)\b"
    r"|\bfrom\s+(?:scratch|zero|0)\b",
    re.IGNORECASE,
)


def _included(path: Path, docs_dir: str) -> bool:
    parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    shared = any(
        path.is_relative_to(Path(item)) or Path(item).is_relative_to(path)
        for item in SHARED_PROJECT_PATHS
    )
    return not (
        any(part in _EXCLUDED_DIRS or part.endswith(".egg-info") for part in parts)
        or is_review_report(path, Path(docs_dir))
        or (parts[0] == ".meow" and not shared)
        or name == ".env"
        or name.startswith(".env.")
        or name in _PRIVATE_NAMES
        or path.suffix.lower() in _PRIVATE_SUFFIXES | _GENERATED_SUFFIXES
        or name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
    )


def _paths(root: Path, docs_dir: str) -> list[Path]:
    git = shutil.which("git")
    if git:
        result = subprocess.run(
            [git, "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return sorted({
                Path(os.fsdecode(p)) for p in result.stdout.split(b"\0") if p
            })
        if (root / ".git").exists():
            raise OSError("Cannot inventory the Git working tree")
    paths = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        parent = Path(directory).relative_to(root)
        dirs[:] = [name for name in dirs if _included(parent / name, docs_dir)]
        paths.extend(parent / name for name in names)
    return sorted(paths)


def _inventory(root: Path, docs_dir: str) -> tuple[dict, dict]:  # ruff: ignore[complex-structure, too-many-statements] -- inventory text sources within checkout boundaries
    areas: dict[str, list[str]] = {}
    files = {}
    for relative in _paths(root, docs_dir):
        path = root / relative
        if not _included(relative, docs_dir) or not path.is_file():
            continue
        # A symlink must never cause a review to read outside the checkout.
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        content = path.read_bytes()
        try:
            source = content.decode("utf-8")
        except UnicodeError:
            continue
        if "\0" in source or not source.strip():
            continue
        name = relative.as_posix()
        parts = relative.parts
        area = "." if len(parts) == 1 else parts[0]
        if area in _CONTAINERS and len(parts) > _AREA_DEPTH:
            area = "/".join(parts[:2])
        areas.setdefault(area, []).append(name)
        files[name] = hashlib.sha256(content).hexdigest()
    return areas, files


def resolve_scope(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- shared SDK/native contract
    root: Path, docs_dir: str, basis: str | None, has_diff: bool, scope: str = "auto"
) -> dict:
    """Resolve intent independently of a dirty diff and snapshot project areas."""
    if scope not in {"auto", "project", "diff"}:
        raise ValueError(f"Unknown review scope: {scope}")
    basis = (basis or "").strip()
    mode = scope
    if mode == "auto":
        mode = (
            "project"
            if _WHOLE_PROJECT.search(basis) or not (basis or has_diff)
            else "diff"
        )
    root = root.resolve()
    areas, files = _inventory(root, docs_dir)
    return {
        "mode": mode,
        "root": str(root),
        "docs_dir": docs_dir,
        "areas": areas,
        "files": files,
    }


def save_scope(review_file: Path, scope: dict) -> Path:
    """Save the review's starting inventory beside its verdict."""
    sidecar = review_file.with_suffix(review_file.suffix + ".scope.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(scope, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return sidecar


def saved_scope_mode(review_file: Path) -> str:
    """Preserve a resumed review's project contract; invalid metadata widens it."""
    sidecar = review_file.with_suffix(review_file.suffix + ".scope.json")
    if not sidecar.exists():
        return "auto"
    try:
        scope = json.loads(sidecar.read_text(encoding="utf-8"))
        return "diff" if scope["mode"] == "diff" else "project"
    except (OSError, ValueError, KeyError, TypeError):
        return "project"


def prepare_scope(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- shared SDK/native contract
    review_file: Path,
    root: Path,
    docs_dir: str,
    basis: str | None,
    has_diff: bool,
    scope: str = "auto",
) -> dict:
    resolved = resolve_scope(root, docs_dir, basis, has_diff, scope)
    save_scope(review_file, resolved)
    return resolved


def scope_instructions(scope: dict) -> str:
    if scope["mode"] != "project":
        return ""
    return (
        "\n\nCode-owned review scope: PROJECT. Review the whole project; a dirty "
        "diff does not narrow this scope. Inspect every listed repository area, "
        "including frontend, backend, scripts, skills, tests, and root files. "
        "Do not modify the scope sidecar. Missing, invalid, or stale coverage "
        "forces FAIL. For EACH area append a plain single-line COVERAGE: JSON "
        'record: {"area":"frontend","path":"frontend/app.tsx",'
        '"line":1,"excerpt":"exact source line"}. '
        "Use an inventoried path in that area, a 1-based line number, and the exact "
        "nonblank source line with surrounding whitespace stripped. Read the file "
        "before citing it. These records accompany your findings. "
        "Inventory (area -> repository-relative paths):\n"
        + json.dumps(scope["areas"], sort_keys=True, indent=2)
    )


def _record_error(record: dict, scope: dict) -> str | None:  # ruff: ignore[too-many-return-statements] -- validate grounded evidence fields
    area, name = record.get("area"), record.get("path")
    if not isinstance(area, str) or area not in scope["areas"]:
        return "Coverage names an unknown repository area"
    if name not in scope["areas"][area]:
        return f"Coverage for {area} names a file outside its inventory"
    line, excerpt = record.get("line"), record.get("excerpt")
    if (
        type(line) is not int
        or line < 1
        or not isinstance(excerpt, str)
        or not excerpt.strip()
    ):
        return f"Coverage for {area} requires a positive line and nonblank excerpt"
    try:
        lines = (Path(scope["root"]) / name).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return f"Coverage source for {area} is unreadable"
    if line > len(lines) or lines[line - 1].strip() != excerpt:
        return f"Coverage for {area} does not match the cited source line"
    return None


def _coverage_records(text: str, scope: dict) -> list[str]:
    errors, covered = [], set()
    for payload in re.findall(r"^\s*COVERAGE:\s*(.*)$", text, re.MULTILINE):
        try:
            record = json.loads(payload)
        except (ValueError, TypeError):
            errors.append("Invalid COVERAGE JSON record")
            continue
        error = (
            _record_error(record, scope)
            if isinstance(record, dict)
            else "Invalid COVERAGE object"
        )
        if error:
            errors.append(error)
        else:
            covered.add(record["area"])
    return errors + [
        f"Missing grounded coverage for {area}"
        for area in sorted(set(scope["areas"]) - covered)
    ]


def coverage_errors(review_file: Path, text: str, *, required=False) -> list[str]:  # ruff: ignore[too-many-return-statements] -- fail-closed sidecar validation
    """Reject absent/fabricated coverage and repository changes since preparation.

    Reports without a sidecar predate project coverage or belong to plan/diff/
    remote reviews; callers preparing PROJECT reviews must preserve the sidecar.
    """
    sidecar = review_file.with_suffix(review_file.suffix + ".scope.json")
    if not sidecar.exists():
        return ["Required project coverage inventory is missing"] if required else []
    try:
        scope = json.loads(sidecar.read_text(encoding="utf-8"))
        if scope["mode"] not in {"project", "diff"}:
            raise ValueError("invalid scope mode")
        areas, files = _inventory(Path(scope["root"]), scope["docs_dir"])
        if areas != scope["areas"] or files != scope["files"]:
            return [
                "Project inventory changed after review preparation; rerun the review"
            ]
        if scope["mode"] == "diff":
            return []
        return _coverage_records(text, scope)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return ["Review scope inventory is unreadable or invalid; rerun the review"]

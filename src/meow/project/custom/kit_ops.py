"""Add, update, and remove kits; the work behind `meow kits`."""

import shutil
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tomlkit

from meow.project.config_files import CONFIG_FILENAME, LOCAL_CONFIG_FILENAME
from meow.project.custom.kit_store import (
    KitUnavailable,
    default_name,
    fetch,
    kit_dir,
    normalize_url,
    remote_sha,
    tree,
)
from meow.project.custom.kits import (
    NAME,
    KitSpec,
    contents,
    kits_home,
    lock_file,
    parse_kits,
    read_lock,
    validate_kit,
    write_lock,
)
from meow.project.custom.sources import KINDS

SCOPES = ("local", "project", "user")


def config_file(scope: str, project_dir: Path, user_dir: Path) -> Path:
    if scope == "user":
        return Path(user_dir) / CONFIG_FILENAME.removeprefix(".meow/")
    name = LOCAL_CONFIG_FILENAME if scope == "local" else CONFIG_FILENAME
    return Path(project_dir) / name


def collect_specs(project_dir: Path, user_dir: Path) -> list[KitSpec]:
    """Every declared kit, read without loading the rest of the configuration."""
    specs: list[KitSpec] = []
    for scope in SCOPES:
        path = config_file(scope, project_dir, user_dir)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        specs += parse_kits(data.get("kits"), scope, path)
    return specs


def _lock(spec: KitSpec, project_dir: Path, user_dir: Path) -> Path:
    return lock_file(spec.scope, project_dir, user_dir)


def hook_lines(root: Path) -> list[str]:
    lines = []
    for path in sorted((root / "hooks").glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        lines.append(f"{path.stem} ({data.get('event')}): {data.get('command')}")
    return lines


def describe(root: Path) -> list[str]:
    found = contents(root)
    lines = [f"  {kind + ':':<11}{', '.join(names)}" for kind, names in found.items()]
    return [*lines, *(f"    hook runs: {line}" for line in hook_lines(root))]


def fetch_new(url: str, ref: str | None, user_dir: Path) -> tuple[str, str, Path]:
    """Resolve and download a kit before anything is written."""
    remote = normalize_url(url)
    sha = remote_sha(remote, ref)
    root = fetch(kits_home(user_dir), remote, sha)
    validate_kit(root)
    return remote, sha, root


def add_entry(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    path: Path, name: str, url: str, ref: str | None, update: str,
    only: tuple[str, ...], auto_accept_hooks: bool, config: bool,
) -> None:  # fmt: skip
    doc = (
        tomlkit.parse(path.read_text(encoding="utf-8"))
        if path.is_file()
        else (tomlkit.document())
    )
    table = tomlkit.table()
    table["name"] = name
    table["url"] = url
    if ref:
        table["ref"] = ref
    table["update"] = update
    if only:
        table["only"] = list(only)
    if auto_accept_hooks:
        table["auto_accept_hooks"] = True
    if config:
        table["config"] = True
    kits = doc.get("kits")
    if kits is None:
        kits = tomlkit.aot()
        doc["kits"] = kits
    kits.append(table)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def remove_entry(path: Path, name: str) -> bool:
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    kits = doc.get("kits")
    for index, item in enumerate(kits or []):
        if item.get("name") == name:
            del kits[index]
            if not kits:
                del doc["kits"]
            path.write_text(tomlkit.dumps(doc), encoding="utf-8")
            return True
    return False


def install(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    project_dir: Path, user_dir: Path, url: str, *, scope: str = "user",
    name: str | None = None, ref: str | None = None, update: str = "auto",
    only: tuple[str, ...] = (), auto_accept_hooks: bool = False,
    config: bool = False, confirm=None, output=print,
) -> KitSpec | None:  # fmt: skip
    """Fetch a kit, show what it contains, and (once confirmed) declare and pin it."""
    name = name or default_name(normalize_url(url))
    if not NAME.match(name):
        raise ValueError(f"invalid kit name {name!r}; use lowercase letters/digits/-")
    if scope == "project":
        auto_accept_hooks = False
    target = config_file(scope, project_dir, user_dir)
    if any(
        s.name == name and s.scope == scope
        for s in collect_specs(project_dir, user_dir)
    ):
        raise ValueError(f"kit {name!r} is already installed in {scope} scope")
    remote, sha, root = fetch_new(url, ref, user_dir)
    output(f"Fetched {name} @ {sha[:7]} ({ref or 'default branch'}) from {remote}")
    for line in describe(root) or ["  (empty)"]:
        output(line)
    if confirm is not None and not confirm(f"Install into {scope} scope? [y/N] "):
        output("Cancelled; nothing changed.")
        return None
    add_entry(target, name, url, ref, update, only, auto_accept_hooks, config)
    lock = lock_file(scope, project_dir, user_dir)
    kits = read_lock(lock)
    kits[name] = {
        "url": remote, "ref": ref or "HEAD", "sha": sha, "hooks_sha": sha,
        "checked_at": time.time(),
    }  # fmt: skip
    write_lock(lock, kits)
    output(f"Added kit {name!r} to {target}")
    return next(s for s in collect_specs(project_dir, user_dir)
                if s.name == name and s.scope == scope)  # fmt: skip


def refresh_kit(
    spec: KitSpec, entry: dict | None, home: Path, *, apply: bool, accept: bool
) -> tuple[dict, list[str]]:
    """Compare one kit with its remote; with `apply`, move to the new commit.

    Hook changes stay on the old commit unless accepted (`accept`, the kit's
    `auto_accept_hooks`, or a project kit, whose lock change is reviewed in git).
    """
    remote = normalize_url(spec.url)
    latest = remote_sha(remote, spec.ref)
    entry = dict(entry or {})
    old = entry.get("sha")
    entry |= {"url": remote, "ref": spec.ref or "HEAD", "checked_at": time.time()}
    held = entry.get("hooks_sha") or old
    if latest == old:
        fetch(home, remote, old)
        if held != old:
            fetch(home, remote, held)
            if accept or spec.auto_accept_hooks:
                entry["hooks_sha"] = old
                return entry, [f"kit {spec.name}: hook changes accepted"]
            return entry, _held(spec, home, remote, old)
        return entry, []
    if not apply:
        shown = old[:7] if old else "none"
        return entry, [f"kit {spec.name}: update available ({shown} -> {latest[:7]})"]
    root = fetch(home, remote, latest)
    validate_kit(root, spec.only)
    entry["sha"] = latest
    same_hooks = held is not None and tree(kit_dir(home, remote, held) / "hooks") == (
        tree(root / "hooks")
    )
    if (
        old is None
        or same_hooks
        or accept
        or spec.auto_accept_hooks
        or (spec.scope == "project")
    ):
        entry["hooks_sha"] = latest
        return entry, [f"kit {spec.name}: updated to {latest[:7]}"]
    entry["hooks_sha"] = held
    fetch(home, remote, held)
    return entry, [
        f"kit {spec.name}: updated to {latest[:7]}",
        *_held(spec, home, remote, latest),
    ]


def _held(spec: KitSpec, home: Path, remote: str, sha: str) -> list[str]:
    root = kit_dir(home, remote, sha)
    return [
        f"kit {spec.name}: hook changes are on hold; they would run:",
        *(f"    {line}" for line in hook_lines(root)),
        f"    approve with: meow kits update {spec.name} --accept",
    ]


def update(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    project_dir: Path, user_dir: Path, names: tuple[str, ...] = (), *,
    check: bool = False, accept: bool = False,
) -> list[str]:  # fmt: skip
    """The manual `meow kits update`: always checks the remote, ignoring the timer."""
    return _run(project_dir, user_dir, names, due_only=False, apply=not check,
                accept=accept, manual=True)  # fmt: skip


def auto_update(
    project_dir: Path, user_dir: Path, now: float | None = None
) -> list[str]:
    """Startup check: only kits whose timer ran out; project kits just report."""
    return _run(project_dir, user_dir, (), due_only=True, apply=True, accept=False,
                manual=False, now=now)  # fmt: skip


def _run(  # ruff: ignore[too-many-arguments, too-many-locals]
    project_dir, user_dir, names, *, due_only, apply, accept, manual, now=None
) -> list[str]:  # fmt: skip
    now = time.time() if now is None else now
    home = kits_home(user_dir)
    specs = [s for s in collect_specs(project_dir, user_dir)
             if not names or s.name in names]  # fmt: skip
    if names and (missing := sorted(set(names) - {s.name for s in specs})):
        raise ValueError(f"no such kit: {', '.join(missing)}")
    locks = {s.scope: read_lock(_lock(s, project_dir, user_dir)) for s in specs}

    def work(spec: KitSpec) -> tuple[KitSpec, dict | None, list[str]]:
        entry = locks[spec.scope].get(spec.name)
        due = (
            not due_only
            or entry is None
            or (now - entry.get("checked_at", 0) >= spec.check_every)
        )
        try:
            if spec.update == "pinned" and entry:
                if entry.get("sha"):
                    fetch(home, normalize_url(spec.url), entry["sha"])
                return spec, None, []
            if not due:
                fetch(home, normalize_url(spec.url), entry["sha"])  # cache only
                return spec, None, []
            project_kit = spec.scope == "project" and entry is not None
            new, lines = refresh_kit(
                spec, entry, home,
                apply=apply and (manual or not project_kit),
                accept=accept,
            )  # fmt: skip
            return spec, new, lines
        except KitUnavailable as exc:
            return spec, None, [f"kit {spec.name}: offline ({exc})"] if manual else []
        except ValueError as exc:
            return spec, None, [f"kit {spec.name}: {exc}"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(work, specs))
    lines: list[str] = []
    for spec, entry, found in results:
        if entry is not None:
            locks[spec.scope][spec.name] = entry
        lines += found
    for scope, data in locks.items():
        write_lock(lock_file(scope, project_dir, user_dir), data)
    if manual and not lines:
        lines.append("All kits are up to date.")
    return lines


def uninstall(
    project_dir: Path, user_dir: Path, name: str, *, scope: str | None = None,
    keep_cache: bool = False,
) -> list[str]:  # fmt: skip
    matches = [s for s in collect_specs(project_dir, user_dir)
               if s.name == name and scope in (None, s.scope)]  # fmt: skip
    if not matches:
        raise ValueError(
            f"no kit named {name!r}" + (f" in {scope} scope" if scope else "")
        )
    if len(matches) > 1:
        scopes = ", ".join(s.scope for s in matches)
        raise ValueError(f"kit {name!r} is installed in {scopes}; pass --scope")
    spec = matches[0]
    lock = _lock(spec, project_dir, user_dir)
    kits = read_lock(lock)
    entry = kits.pop(name, {})
    remove_entry(spec.config_file, name)
    write_lock(lock, kits)
    lines = [f"Removed kit {name!r} from {spec.config_file}"]
    if not keep_cache:
        lines += _prune_cache(project_dir, user_dir, normalize_url(spec.url), entry)
    return lines


def _prune_cache(
    project_dir: Path, user_dir: Path, remote: str, entry: dict
) -> list[str]:
    home = kits_home(user_dir)
    used = {
        sha
        for s in collect_specs(project_dir, user_dir)
        for sha in (
            read_lock(_lock(s, project_dir, user_dir)).get(s.name) or {}
        ).values()
        if isinstance(sha, str)
    }
    gone = []
    for sha in {entry.get("sha"), entry.get("hooks_sha")} - {None} - used:
        folder = kit_dir(home, remote, sha)
        if folder.is_dir():
            shutil.rmtree(folder)
            gone.append(sha[:7])
    return [f"Deleted cached copy {', '.join(sorted(gone))}"] if gone else []


def listing(project_dir: Path, user_dir: Path) -> list[str]:
    home = kits_home(user_dir)
    lines = []
    for spec in collect_specs(project_dir, user_dir):
        entry = read_lock(_lock(spec, project_dir, user_dir)).get(spec.name) or {}
        sha = entry.get("sha")
        fetched = bool(sha) and kit_dir(home, normalize_url(spec.url), sha).is_dir()
        state = f"@ {sha[:7]}" if fetched else "not downloaded"
        hold = " (hook changes on hold)" if entry.get("hooks_sha") != sha else ""
        lines.append(
            f"{spec.name:<20} {spec.scope:<8} {spec.update:<7} {state}{hold}  {spec.url}"
        )
    return lines or ["No kits installed. Add one: meow kits add URL"]


def show(project_dir: Path, user_dir: Path, name: str) -> list[str]:
    for spec in collect_specs(project_dir, user_dir):
        if spec.name == name:
            entry = read_lock(_lock(spec, project_dir, user_dir)).get(name) or {}
            root = kit_dir(
                kits_home(user_dir), normalize_url(spec.url), entry.get("sha", "-")
            )
            body = (
                describe(root)
                if root.is_dir()
                else ["  not downloaded: meow kits update"]
            )
            return [f"{name} ({spec.scope} scope) {spec.url}", *body]
    raise ValueError(f"no kit named {name!r}")


__all__ = ["KINDS", "install", "update", "auto_update", "uninstall", "listing", "show"]

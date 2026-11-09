"""Add, update, and remove kits; the work behind `meow kits`."""

import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from meow.project.custom import kit_agents
from meow.project.custom.kit_pins import active_run_shas
from meow.project.custom.kit_store import (
    KitUnavailable,
    default_name,
    fetch,
    kit_dir,
    normalize_url,
    remote_sha,
    remove_tree,
    tree,
)
from meow.project.custom.kits import (
    NAME,
    KitSpec,
    collect_specs,
    config_file,
    contents,
    effective_entry,
    kits_home,
    lock_file,
    read_lock,
    validate_kit,
    write_lock,
)

# The startup hook is killed after 30 s, and one kit may need three git calls.
STARTUP_TIMEOUT = 8


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


def set_rev(path: Path, name: str, sha: str) -> None:
    """Write a project kit's pinned commit into the committed config."""
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    for item in doc.get("kits") or []:
        if item.get("name") == name:
            item["rev"] = sha
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


def _declared(project_dir: Path, user_dir: Path, name: str, scope: str) -> bool:
    return any(
        s.name == name and s.scope == scope
        for s in collect_specs(project_dir, user_dir)
    )


def install(  # ruff: ignore[too-many-arguments]
    project_dir: Path,
    user_dir: Path,
    url: str,
    *,
    scope: str = "user",
    name: str | None = None,
    ref: str | None = None,
    update: str = "auto",
    only: tuple[str, ...] = (),
    auto_accept_hooks: bool = False,
    config: bool = False,
    confirm=None,
    output=print,
) -> KitSpec | None:
    """Fetch a kit, show what it contains, and (once confirmed) declare and pin it."""
    name = name or default_name(normalize_url(url))
    if not NAME.match(name):
        raise ValueError(f"invalid kit name {name!r}; use lowercase letters/digits/-")
    if _declared(project_dir, user_dir, name, scope):
        raise ValueError(f"kit {name!r} is already installed in {scope} scope")
    remote, sha, root = fetch_new(url, ref, user_dir)
    output(f"Fetched {name} @ {sha[:7]} ({ref or 'default branch'}) from {remote}")
    for line in describe(root) or ["  (empty)"]:
        output(line)
    if confirm is not None and not confirm(f"Install into {scope} scope? [y/N] "):
        output("Cancelled; nothing changed.")
        return None
    target = config_file(scope, project_dir, user_dir)
    accept = auto_accept_hooks and scope != "project"
    add_entry(target, name, url, ref, update, only, accept, config)
    if scope == "project":
        set_rev(target, name, sha)
    _pin(lock_file(scope, project_dir, user_dir), name, remote, (ref, sha))
    _approve_installed(lock_file(scope, project_dir, user_dir), name, root, only)
    output(f"Added kit {name!r} to {target}")
    return next(
        s
        for s in collect_specs(project_dir, user_dir)
        if s.name == name and s.scope == scope
    )


def _pin(lock: Path, name: str, remote: str, ref_sha: tuple[str | None, str]) -> None:
    ref, sha = ref_sha
    kits = read_lock(lock)
    kits[name] = {
        "url": remote,
        "ref": ref or "HEAD",
        "sha": sha,
        "hooks_sha": sha,
        "checked_at": time.time(),
    }
    write_lock(lock, kits)


def _approve_installed(lock: Path, name: str, root: Path, only: tuple[str, ...]):
    """Installing shows the kit's contents and asks, so its agents start approved."""
    kits = read_lock(lock)
    kits[name][kit_agents.LOCK_KEY] = kit_agents.approved_of(
        kit_agents.kit_agent_tools(root, only)
    )
    write_lock(lock, kits)


@dataclass(frozen=True)
class Kit:
    """A declared kit together with where its commits are cached."""

    spec: KitSpec
    home: Path
    timeout: float = 120  # seconds per git call; startup passes a short one

    @property
    def remote(self) -> str:
        return normalize_url(self.spec.url)

    def fetch(self, sha: str) -> Path:
        return fetch(self.home, self.remote, sha, self.timeout)

    def root(self, sha: str) -> Path:
        return kit_dir(self.home, self.remote, sha)


def _hook_lines_held(kit: Kit, sha: str) -> list[str]:
    spec = kit.spec
    root = kit.root(sha)
    return [
        f"kit {spec.name}: hook changes are on hold; they would run:",
        *(f"    {line}" for line in hook_lines(root)),
        f"    approve with: meow kits update {spec.name} --accept",
    ]


def _trusts_hooks(spec: KitSpec, accept: bool) -> bool:
    """Hook changes apply when approved, trusted, or reviewed in a project commit."""
    return accept or spec.auto_accept_hooks or spec.scope == "project"


def _settle_hooks(kit: Kit, entry: dict, accept: bool) -> list[str]:
    """The kit is current; apply or keep holding hook changes from earlier."""
    sha = entry["sha"]
    held = entry.get("hooks_sha") or sha
    kit.fetch(sha)
    if held == sha:
        return []
    kit.fetch(held)
    if accept or kit.spec.auto_accept_hooks:
        entry["hooks_sha"] = sha
        return [f"kit {kit.spec.name}: hook changes accepted"]
    return _hook_lines_held(kit, sha)


def _settle_agents(kit: Kit, entry: dict, accept: bool) -> list[str]:
    """Approve, or hold, agents the kit's current commit would newly sync."""
    spec = kit.spec
    tools = kit_agents.kit_agent_tools(kit.root(entry["sha"]), spec.only)
    approved = dict(entry.get(kit_agents.LOCK_KEY) or {})
    waiting = kit_agents.pending(tools, approved)
    if _trusts_hooks(spec, accept):
        entry[kit_agents.LOCK_KEY] = kit_agents.approved_of(tools)
        return [f"kit {spec.name}: agent changes accepted"] * bool(waiting)
    entry[kit_agents.LOCK_KEY] = {
        name: wanted for name, wanted in approved.items() if name in tools
    }
    return kit_agents.hold_lines(spec.name, waiting) if waiting else []


def _advance(kit: Kit, entry: dict, latest: str, accept: bool) -> list[str]:
    """Move the kit to `latest`; hook changes may stay on the previous commit."""
    spec = kit.spec
    old = entry.get("sha")
    held = entry.get("hooks_sha") or old
    root = kit.fetch(latest)
    validate_kit(root, spec.only)
    entry["sha"] = latest
    unchanged = held is not None and tree(kit.root(held) / "hooks") == (
        tree(root / "hooks")
    )
    if old is None or unchanged or _trusts_hooks(spec, accept):
        entry["hooks_sha"] = latest
        return [f"kit {spec.name}: updated to {latest[:7]}"]
    kit.fetch(held)
    entry["hooks_sha"] = held
    return [
        f"kit {spec.name}: updated to {latest[:7]}",
        *_hook_lines_held(kit, latest),
    ]


def refresh_kit(
    kit: Kit, entry: dict | None, *, apply: bool, accept: bool
) -> tuple[dict, list[str]]:
    """Compare one kit with its remote; with `apply`, move to the new commit."""
    spec, remote = kit.spec, kit.remote
    latest = remote_sha(remote, spec.ref, kit.timeout)
    entry = {**(entry or {}), "url": remote, "ref": spec.ref or "HEAD"}
    entry["checked_at"] = time.time()
    if latest == entry.get("sha"):
        entry.pop("latest", None)
        lines = _settle_hooks(kit, entry, accept)
        return entry, [*lines, *_settle_agents(kit, entry, accept)]
    if not apply:
        entry["latest"] = latest  # remembered, so `list` and startup can say so
        return entry, []
    entry.pop("latest", None)
    lines = _advance(kit, entry, latest, accept)
    return entry, [*lines, *_settle_agents(kit, entry, accept)]


@dataclass(frozen=True)
class Plan:
    """How one `update` or startup check treats the kits it visits."""

    home: Path
    now: float
    manual: bool  # an explicit `meow kits update`, not the startup check
    apply: bool = True
    accept: bool = False
    timeout: float = 120

    def due(self, spec: KitSpec, entry: dict | None) -> bool:
        if self.manual or entry is None:
            return True
        return self.now - entry.get("checked_at", 0) >= spec.check_every

    def pinned(self, spec: KitSpec, entry: dict | None) -> bool:
        return bool(entry) and spec.update == "pinned" and not self.manual


def _check(spec: KitSpec, entry: dict | None, plan: Plan) -> tuple[dict | None, list]:
    kit = Kit(spec, plan.home, plan.timeout)
    if entry and entry.get("sha"):  # the pinned copy must be on disk
        kit.fetch(entry["sha"])
    if plan.pinned(spec, entry) or not plan.due(spec, entry):
        return None, []
    follows_lock = spec.scope == "project" and entry is not None
    apply = plan.apply and (plan.manual or not follows_lock)
    return refresh_kit(kit, entry, apply=apply, accept=plan.accept)


def _safe_check(spec: KitSpec, entry: dict | None, plan: Plan):
    """One kit's check; an unreachable remote is only news for a manual update."""
    try:
        return _check(spec, entry, plan)
    except KitUnavailable as exc:
        return None, [
            f"kit {spec.name}: could not reach or fetch ({exc})"
        ] * plan.manual
    except ValueError as exc:
        return None, [f"kit {spec.name}: {exc}"]


def _select(project_dir: Path, user_dir: Path, names: tuple[str, ...]):
    specs = [
        s for s in collect_specs(project_dir, user_dir) if not names or s.name in names
    ]
    if missing := sorted(set(names) - {s.name for s in specs}):
        raise ValueError(f"no such kit: {', '.join(missing)}")
    return specs


def _available(spec: KitSpec, entry: dict) -> list[str]:
    latest, sha = entry.get("latest"), entry.get("sha")
    if not latest or latest == sha:
        return []
    return [
        f"kit {spec.name}: update available ({(sha or 'none')[:7]} -> {latest[:7]})"
    ]


def pending_updates(project_dir: Path, user_dir: Path) -> list[str]:
    """Updates found earlier and not applied, for the startup message."""
    lines = []
    for spec in collect_specs(project_dir, user_dir):
        entry = _entry(spec, project_dir, user_dir)
        latest, sha = entry.get("latest"), entry.get("sha")
        if not latest or not sha or latest == sha:
            continue
        action = f"run `meow kits update {spec.name}`"
        if spec.scope == "project":
            action += f", review it, and commit {spec.config_file.name}"
        lines.append(f"  {spec.name}: {sha[:7]} -> {latest[:7]}; {action}")
    return ["Kit updates available:", *lines] if lines else []


def _write_rev(spec: KitSpec, entry: dict, old: dict | None, plan: Plan) -> list[str]:
    """Record a project kit's new commit in the committed config."""
    moved = entry.get("sha") != spec.rev
    if spec.scope != "project" or not moved or not (plan.manual or old is None):
        return []
    set_rev(spec.config_file, spec.name, entry["sha"])
    return [
        f"kit {spec.name}: rev is now {entry['sha'][:7]}; commit {spec.config_file}"
    ]


def _visit(project_dir: Path, user_dir: Path, names: tuple[str, ...], plan: Plan):
    specs = _select(project_dir, user_dir, names)
    locks = {s.scope: read_lock(_lock(s, project_dir, user_dir)) for s in specs}
    before = {s.name: locks[s.scope].get(s.name) for s in specs}

    def effective(spec: KitSpec) -> dict | None:
        return effective_entry(spec, before[spec.name])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: _safe_check(s, effective(s), plan), specs))
    lines: list[str] = []
    for spec, (entry, found) in zip(specs, results, strict=True):
        if entry is not None:
            locks[spec.scope][spec.name] = entry
            lines += _write_rev(spec, entry, before[spec.name], plan)
            lines += _available(spec, entry) if plan.manual and not plan.apply else []
        lines += found
    for scope, data in locks.items():
        write_lock(lock_file(scope, project_dir, user_dir), data)
    for spec in specs:
        _prune_cache(project_dir, user_dir, normalize_url(spec.url))
    return lines


def update(  # ruff: ignore[too-many-arguments]
    project_dir: Path,
    user_dir: Path,
    names: tuple[str, ...] = (),
    *,
    check: bool = False,
    accept: bool = False,
) -> list[str]:
    """The manual `meow kits update`: always checks the remote, ignoring the timer."""
    plan = Plan(kits_home(user_dir), time.time(), True, not check, accept)
    return _visit(project_dir, user_dir, names, plan) or ["All kits are up to date."]


def auto_update(
    project_dir: Path, user_dir: Path, now: float | None = None
) -> list[str]:
    """Startup check: only kits whose timer ran out; project kits just report."""
    now = time.time() if now is None else now
    plan = Plan(kits_home(user_dir), now, False, timeout=STARTUP_TIMEOUT)
    return _visit(project_dir, user_dir, (), plan)


def uninstall(  # ruff: ignore[too-many-arguments]
    project_dir: Path,
    user_dir: Path,
    name: str,
    *,
    scope: str | None = None,
    keep_cache: bool = False,
) -> list[str]:
    matches = [
        s
        for s in collect_specs(project_dir, user_dir)
        if s.name == name and (scope is None or s.scope == scope)
    ]
    if not matches:
        where = f" in {scope} scope" if scope else ""
        raise ValueError(f"no kit named {name!r}{where}")
    if len(matches) > 1:
        scopes = ", ".join(s.scope for s in matches)
        raise ValueError(f"kit {name!r} is installed in {scopes}; pass --scope")
    spec = matches[0]
    lock = _lock(spec, project_dir, user_dir)
    kits = read_lock(lock)
    kits.pop(name, None)
    remove_entry(spec.config_file, name)
    write_lock(lock, kits)
    lines = [f"Removed kit {name!r} from {spec.config_file}"]
    if not keep_cache:
        lines += _prune_cache(project_dir, user_dir, normalize_url(spec.url))
    return lines


def _pinned_shas(project_dir: Path, user_dir: Path, remote: str) -> set[str]:
    shas = set()
    for s in collect_specs(project_dir, user_dir):
        if normalize_url(s.url) != remote:
            continue
        entry = read_lock(_lock(s, project_dir, user_dir)).get(s.name) or {}
        shas |= {v for v in entry.values() if isinstance(v, str)}
        shas |= {s.rev} if s.rev else set()
    return shas | active_run_shas(project_dir, remote)


def _prune_cache(project_dir: Path, user_dir: Path, remote: str) -> list[str]:
    """Delete cached commits of `remote` that no lock file here still pins."""
    used = _pinned_shas(project_dir, user_dir, remote)
    repo = kit_dir(kits_home(user_dir), remote, "x").parent
    gone = []
    for folder in repo.iterdir() if repo.is_dir() else ():
        if folder.is_dir() and folder.name not in used:
            remove_tree(folder)
            gone.append(folder.name[:7])
    if repo.is_dir() and not any(repo.iterdir()):
        repo.rmdir()
    return [f"Deleted cached copy {', '.join(sorted(gone))}"] if gone else []


def _entry(spec: KitSpec, project_dir: Path, user_dir: Path) -> dict:
    locked = read_lock(_lock(spec, project_dir, user_dir)).get(spec.name)
    return effective_entry(spec, locked) or {}


def listing(project_dir: Path, user_dir: Path) -> list[str]:
    home = kits_home(user_dir)
    lines = []
    for spec in collect_specs(project_dir, user_dir):
        entry = _entry(spec, project_dir, user_dir)
        sha = entry.get("sha")
        fetched = bool(sha) and kit_dir(home, normalize_url(spec.url), sha).is_dir()
        state = f"@ {sha[:7]}" if fetched else "not downloaded"
        if entry.get("latest") and entry["latest"] != sha:
            state += f" (update available: {entry['latest'][:7]})"
        hold = " (hook changes on hold)" if entry.get("hooks_sha") != sha else ""
        head = f"{spec.name:<20} {spec.scope:<8} {spec.update:<7}"
        lines.append(f"{head} {state}{hold}  {spec.url}")
    return lines or ["No kits installed. Add one: meow kits add URL"]


def show(project_dir: Path, user_dir: Path, name: str) -> list[str]:
    for spec in collect_specs(project_dir, user_dir):
        if spec.name == name:
            entry = _entry(spec, project_dir, user_dir)
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


__all__ = [
    "auto_update",
    "install",
    "listing",
    "pending_updates",
    "show",
    "uninstall",
    "update",
]

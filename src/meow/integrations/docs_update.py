"""Evidence and safety boundaries for manual documentation updates."""

import json
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

MARKER = Path("docs/.meow-docs-update.json")
MAX_EVIDENCE_CHARS = 120_000
SHA_HEX_LENGTH = 40


class DocsUpdateError(ValueError):
    """A documentation update cannot safely proceed."""


@dataclass(frozen=True)
class DocsUpdateInput:
    repo: Path
    baseline_sha: str
    head_sha: str
    changed_paths: tuple[str, ...]
    diff: str


@dataclass(frozen=True)
class DocsUpdateResult:
    baseline_sha: str
    head_sha: str
    changed_paths: tuple[str, ...]
    diff: str


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and result.returncode:
        raise DocsUpdateError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def _changed_paths(repo: Path) -> tuple[str, ...]:
    output = _git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    return tuple(line[3:] for line in output.splitlines() if line)


def _is_doc_path(path: str) -> bool:
    candidate = Path(path)
    return path == "README.md" or (
        candidate.suffix.lower() in {".md", ".rst", ".txt"}
        and not candidate.is_absolute()
        and ".." not in candidate.parts
        and candidate.parts[0] == "docs"
    )


def prepare_docs_update(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    repo: Path, since: str | None
) -> DocsUpdateInput:
    """Validate a clean dev checkout and resolve the bounded evidence window."""
    repo = repo.resolve()
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != "dev":
        raise DocsUpdateError("docs-update must run on the dev branch")
    if _changed_paths(repo):
        raise DocsUpdateError("docs-update requires a clean working tree")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    marker = repo / MARKER
    if marker.exists():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            saved_head = saved["inspected_head"]
            if not isinstance(saved_head, str) or len(saved_head) != SHA_HEX_LENGTH:
                raise ValueError("invalid inspected_head")
        except (OSError, ValueError, KeyError) as exc:
            raise DocsUpdateError("invalid docs-update baseline marker") from exc
        if since is not None:
            raise DocsUpdateError("--since is only valid before the first docs-update")
        baseline_ref = saved_head
    else:
        if since is None:
            raise DocsUpdateError("first docs-update requires --since REF")
        baseline_ref = since
    resolved = _git(
        repo, "rev-parse", "--verify", f"{baseline_ref}^{{commit}}", check=False
    )
    if resolved.returncode:
        raise DocsUpdateError(f"docs-update baseline is invalid: {baseline_ref}")
    baseline = resolved.stdout.strip()
    if _git(
        repo, "merge-base", "--is-ancestor", baseline, head, check=False
    ).returncode:
        raise DocsUpdateError("docs-update baseline is not an ancestor of HEAD")
    paths = tuple(
        path
        for path in _git(
            repo, "diff", "--name-only", baseline, head
        ).stdout.splitlines()
        if path
    )
    diff = _git(
        repo, "diff", "--no-ext-diff", "--find-renames", baseline, head, "--", "."
    ).stdout
    if len(diff) > MAX_EVIDENCE_CHARS:
        raise DocsUpdateError(
            "comparison diff is too large; choose a closer --since baseline"
        )
    return DocsUpdateInput(repo, baseline, head, paths, diff)


async def run_docs_update(  # ruff: ignore[complex-structure, too-many-statements]
    prepared: DocsUpdateInput,
    agent: Callable[[DocsUpdateInput], Awaitable[None]] | None = None,
) -> DocsUpdateResult:
    """Invoke the documentation role, validate its edits, and advance the marker."""
    if _git(
        prepared.repo, "rev-parse", "HEAD"
    ).stdout.strip() != prepared.head_sha or _changed_paths(prepared.repo):
        raise DocsUpdateError("repository changed after docs-update preflight")
    if agent is None:
        from meow.agents.docs_updater import update_documentation

        agent = update_documentation
    await agent(prepared)
    changed = _changed_paths(prepared.repo)
    invalid = [path for path in changed if not _is_doc_path(path)]
    if invalid:
        raise DocsUpdateError(
            "agent changed files outside documentation: " + ", ".join(invalid)
        )
    marker = prepared.repo / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "baseline_sha": prepared.baseline_sha,
                "inspected_head": prepared.head_sha,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    diff = _git(prepared.repo, "diff", "--", "README.md", "docs").stdout
    for path in (*changed, MARKER.as_posix()):
        if not (prepared.repo / path).is_file():
            continue
        if (
            _git(
                prepared.repo, "ls-files", "--error-unmatch", "--", path, check=False
            ).returncode
            == 0
        ):
            continue
        content = (prepared.repo / path).read_text(encoding="utf-8")
        diff += "".join(
            unified_diff(
                [],
                content.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=f"b/{path}",
            )
        )
    return DocsUpdateResult(prepared.baseline_sha, prepared.head_sha, changed, diff)

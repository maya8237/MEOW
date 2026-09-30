# `meow branch-review` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `meow branch-review <branch> --target <target-branch>`: review a local branch's diff against a target branch entirely locally (no GitLab MCP, no MR link), then fix and re-review in a loop until it passes or hits `max_rounds` — in an isolated worktree by default, or in place with `--no-worktree`.

**Architecture:** A new top-level flow module (`branch_reviewer.py`, sibling to `gitlab_reviewer.py`/`review_fix_review.py`) resolves the active directory (a new worktree helper that checks out an *existing* branch, or the current checkout), then loops a new `ReviewerAgent.review_branch(target, branch)` method (computes `git diff <merge-base>` fresh each round, so it sees uncommitted fixes) against `ReviewFixAgent`, reusing `orchestrator._run_prompt_fix_rounds` generalized to take a `re_review` callable instead of hardcoding `review_prompt`. Full native-mode support (a `reviewer-branch` prompt role, a `--existing-branch` prepare flag) and a new `skills/branch-review/SKILL.md` follow the existing dual-mode pattern the test suite enforces.

**Tech Stack:** Python 3.12, argparse, subprocess/git, existing meow agent/orchestrator machinery.

**Spec:** This document — design confirmed with the user in conversation (see task descriptions for the exact decisions and why).

## Global Constraints

- `--target` has no default; it is a required CLI argument (avoid guessing main/dev/master).
- `encoding="utf-8"` on every new `open`/`read_text`/`write_text`/`subprocess.run(text=True)` call (repo-wide convention, enforced by a recent fix).
- Follow existing file-per-responsibility layout: no new cross-cutting abstractions beyond what's specified below.
- Every new skill must carry both `## Native mode` and `## CLI mode` sections and be added to `tests/test_native_skills.py`'s `CLI_FALLBACKS` — enforced by that test file.
- `ruff check .` must stay clean (project uses `preview = true`, `max-complexity = 6`, `max-args = 4`, `max-branches = 7`, `max-statements = 20`).

## Review Focus

- `--target`/`branch` naming a ref that doesn't exist (typo, never pushed) — `git merge-base`/`git diff` fail loudly with a wrapped, clear error rather than a silent empty diff. (Task 3)
- `--no-worktree` used when the current checkout is *not* actually on `<branch>` — fixes would land on the wrong code with no warning. A dedicated check raises before anything runs. (Task 1, Task 6)
- Branch already passes on round 1 (diff has nothing wrong) — the fix loop must not run at all, matching `_run_prompt_fix_rounds`' existing early-return. (Task 6, tested explicitly)
- A worktree already exists from a prior `branch-review` run on the same branch (resumed run) — reused, not recreated or errored on, matching `_ensure_branch_worktree`'s existing precedent. (Task 1)
- A leftover `branch-review.md` later handed to the generic `meow review-fix-review` (which has no target/branch to re-diff against) — rejected with a clear message pointing at `meow branch-review`, not a confusing "plan file not found" error. (Task 5)

---

### Task 1: Worktree helper for an existing branch

**Files:**
- Modify: `src/meow/worktree.py`
- Test: `tests/test_worktree.py` (new)

**Interfaces:**
- Produces: `_ensure_existing_branch_worktree(working_dir: Path, feature_name: str, branch_name: str) -> Path` — checks out an **existing** branch (local, or `origin/<branch_name>`) into `.worktrees/<feature_name>`; raises `RuntimeError` if the branch doesn't exist anywhere, or if `git worktree add` fails. Reuses an already-existing worktree dir at that path (same simple-exists-check style as `_ensure_branch_worktree`).
- Produces: `_require_branch_checked_out(active_dir: Path, branch: str) -> None` — raises `RuntimeError` if `active_dir`'s current `HEAD` branch isn't exactly `branch` (skips the check if git is unavailable). Used by in-place mode.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worktree.py
import subprocess
import tempfile
import unittest
from pathlib import Path

from meow.worktree import _ensure_existing_branch_worktree, _require_branch_checked_out


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(tmp: str) -> Path:
    root = Path(tmp)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


class EnsureExistingBranchWorktreeTests(unittest.TestCase):
    def test_checks_out_an_existing_local_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            worktree_dir = _ensure_existing_branch_worktree(root, "br-feature-x", "feature/x")

            current = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=worktree_dir, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()
            self.assertEqual(current, "feature/x")

    def test_checks_out_a_remote_only_branch(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            origin = make_repo(tmp1)
            root = Path(tmp2)
            git(root.parent, "clone", "-q", str(origin), str(root))
            git(origin, "checkout", "-q", "-b", "feature/remote-only")
            (origin / "README.md").write_text("y\n", encoding="utf-8")
            git(origin, "commit", "-q", "-am", "remote change")
            git(root, "fetch", "-q", "origin")

            worktree_dir = _ensure_existing_branch_worktree(
                root, "br-remote-only", "feature/remote-only"
            )

            current = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=worktree_dir, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()
            self.assertEqual(current, "feature/remote-only")

    def test_reuses_an_existing_worktree_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")
            first = _ensure_existing_branch_worktree(root, "br-feature-x", "feature/x")

            second = _ensure_existing_branch_worktree(root, "br-feature-x", "feature/x")

            self.assertEqual(first, second)

    def test_raises_a_clear_error_when_branch_does_not_exist_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaisesRegex(RuntimeError, "was not found locally or as"):
                _ensure_existing_branch_worktree(root, "br-nope", "does-not-exist")


class RequireBranchCheckedOutTests(unittest.TestCase):
    def test_passes_silently_when_the_branch_matches_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "checkout", "-q", "-b", "feature/x")

            _require_branch_checked_out(root, "feature/x")  # must not raise

    def test_raises_when_head_is_on_a_different_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            with self.assertRaisesRegex(RuntimeError, "feature/x"):
                _require_branch_checked_out(root, "feature/x")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_worktree -v`
Expected: FAIL — `ImportError: cannot import name '_ensure_existing_branch_worktree'`

- [ ] **Step 3: Implement in `src/meow/worktree.py`**

Add both functions after `_ensure_branch_worktree` (before `_push_branch`):

```python
def _ensure_existing_branch_worktree(
    working_dir: Path, feature_name: str, branch_name: str
) -> Path:
    """Check out an EXISTING branch -- local, or `origin/<branch_name>` --
    into its own worktree, for reviewing/fixing a branch that already
    exists rather than creating one.

    Unlike `_ensure_branch_worktree` (creates `branch_name` fresh when it
    doesn't exist yet, for `meow issue`'s new pushable branch) and
    `_ensure_feature_worktree` (a detached, branch-less worktree for
    `run`/`plan`), this requires `branch_name` to already exist and raises
    a clear error otherwise instead of silently creating it.
    """
    worktree_dir = working_dir / ".worktrees" / feature_name
    if worktree_dir.exists():
        return worktree_dir

    git = shutil.which("git")
    if not git:
        raise RuntimeError(
            "git is required for `meow branch-review`'s worktree step."
        )

    (working_dir / ".worktrees").mkdir(parents=True, exist_ok=True)
    local_exists = _run_git(
        [git, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=working_dir,
    ).returncode == 0

    if local_exists:
        argv = [git, "worktree", "add", str(worktree_dir), branch_name]
    else:
        remote_ref = f"refs/remotes/origin/{branch_name}"
        remote_exists = _run_git(
            [git, "rev-parse", "--verify", "--quiet", remote_ref], cwd=working_dir
        ).returncode == 0
        if not remote_exists:
            raise RuntimeError(
                f"Branch '{branch_name}' was not found locally or as "
                f"'origin/{branch_name}'. Fetch it first (`git fetch origin "
                f"{branch_name}`) or check the branch name."
            )
        argv = [
            git, "worktree", "add", "--track", "-b", branch_name,
            str(worktree_dir), f"origin/{branch_name}",
        ]

    result = _run_git(argv, cwd=working_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not create worktree for branch '{branch_name}':\n{result.stderr}"
        )
    return worktree_dir


def _require_branch_checked_out(active_dir: Path, branch: str) -> None:
    """Raise unless `active_dir`'s current HEAD branch is exactly `branch`.

    Guards `branch-review --no-worktree`: fixing "in place" only makes
    sense if the branch under review is what's actually checked out there.
    Skipped (not raised) when git is unavailable -- consistent with
    `_is_linked_worktree`'s own "can't tell, so don't block" fallback.
    """
    git = shutil.which("git")
    if not git:
        return
    result = _run_git([git, "rev-parse", "--abbrev-ref", "HEAD"], cwd=active_dir)
    if result.returncode != 0:
        return
    current = result.stdout.strip()
    if current != branch:
        raise RuntimeError(
            f"--no-worktree requires {active_dir} to already have '{branch}' "
            f"checked out, but HEAD is on '{current}'. Check out '{branch}' "
            "first, or drop --no-worktree to let branch-review check it out "
            "into an isolated worktree instead."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_worktree -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/meow/worktree.py tests/test_worktree.py
git commit -m "Add worktree helper for checking out an existing branch"
```

---

### Task 2: `ProjectContext.use_worktree` constructor kwarg

**Files:**
- Modify: `src/meow/agents/base.py:141-161`
- Test: `tests/test_agents.py` (extend)

**Interfaces:**
- Produces: `ProjectContext.__init__(self, repo_dir, config, *, use_worktree: bool = False)` — was hardcoded `False`; now overridable, matching `Sprint`'s own `use_worktree` field. Default preserves every existing caller's behavior (`gitlab_reviewer.py`, `review_fix_review.py`, `review_runner.py`, `lint_fix.py`, `issue_solver.py` all construct it positionally with 2 args).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agents.py` (any existing test class for `ProjectContext`, or a small new one near the top):

```python
class ProjectContextTests(unittest.TestCase):
    def test_use_worktree_defaults_to_false(self):
        context = ProjectContext(Path("/repo"), {"models": {}, "lint": []})
        self.assertFalse(context.use_worktree)

    def test_use_worktree_can_be_set_true(self):
        context = ProjectContext(
            Path("/repo"), {"models": {}, "lint": []}, use_worktree=True
        )
        self.assertTrue(context.use_worktree)
```

Add `from meow.agents.base import ProjectContext` to the file's imports if not already present (check first — `ReviewerAgentTests` likely already imports it indirectly; import explicitly if needed).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m unittest tests.test_agents.ProjectContextTests -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'use_worktree'`

- [ ] **Step 3: Implement**

In `src/meow/agents/base.py`, change:

```python
    def __init__(self, repo_dir: Path, config: dict):
        self.repo_dir = repo_dir
        self.config = config
        self.use_worktree = False
```

to:

```python
    def __init__(self, repo_dir: Path, config: dict, *, use_worktree: bool = False):
        self.repo_dir = repo_dir
        self.config = config
        self.use_worktree = use_worktree
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_agents -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/meow/agents/base.py tests/test_agents.py
git commit -m "Let ProjectContext take use_worktree explicitly"
```

---

### Task 3: `ReviewerAgent.review_branch` and the branch-diff helper

**Files:**
- Modify: `src/meow/agents/reviewer.py`
- Modify: `src/meow/prompts.py` (add `branch_review_prompt`)
- Test: `tests/test_agents.py` (extend), `tests/test_prompts.py` (extend)

**Interfaces:**
- Consumes: `Agent.options()`, `Agent.run_query()` from `agents/base.py` (unchanged); `lint_instructions`, `verification_instructions`, `architecture_review_instructions`, `_verdict_format` from `prompts.py` (unchanged, already imported there).
- Produces: `BRANCH_REVIEW_FILENAME = "branch-review.md"` (constant in `reviewer.py`, alongside `PROMPT_REVIEW_FILENAME`/`MR_REVIEW_FILENAME`).
- Produces: `_branch_diff(active_dir: Path, target: str, branch: str) -> str` (private helper in `reviewer.py`) — runs `git merge-base target branch`, then `git diff <merge-base>` (one-sided: compares that commit to the current working tree, so it includes any uncommitted `ReviewFixAgent` edits). Raises `RuntimeError` with git's stderr if `merge-base` fails (bad ref).
- Produces: `ReviewerAgent.review_branch(self, target: str, branch: str) -> tuple[str, str]` — writes to `BRANCH_REVIEW_FILENAME` in `docs_dir`, `allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"]` (a real local checkout, unlike `review_merge_request`), returns `(status, verdict_text)` same shape as every other review method.
- Produces (in `prompts.py`): `branch_review_prompt(target: str, branch: str, review_file: Path, lint_commands: list[LintCommand], *, check_worktree_hygiene: bool) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_prompts.py`:

```python
    def test_branch_review_prompt_names_target_and_branch_and_gates_lint(self):
        text = branch_review_prompt(
            "main", "feature/x", Path("branch-review.md"), [],
            check_worktree_hygiene=True,
        )
        self.assertIn("'main'", text)
        self.assertIn("'feature/x'", text)
        self.assertIn("branch-review.md", text)
```

Add `branch_review_prompt` to that file's existing `from meow.prompts import (...)` line.

Add to `tests/test_agents.py`, inside `ReviewerAgentTests` (reuse its `setUp`):

```python
    async def test_review_branch_computes_diff_and_preserves_branch_review_contract(
        self,
    ):
        verdict = "SUMMARY: reviewed\nSTATUS: PASS\nconcern: PASS"

        with (
            patch.object(
                ReviewerAgent, "run_query", new_callable=AsyncMock
            ) as run_query,
            patch(
                "meow.agents.reviewer._branch_diff", return_value="diff text"
            ) as branch_diff,
            patch.object(Path, "read_text", return_value=verdict),
        ):
            status, received_verdict = await ReviewerAgent(self.context).review_branch(
                "main", "feature/x"
            )

        self.assertEqual((status, received_verdict), ("PASS", verdict))
        branch_diff.assert_called_once_with(
            self.context.project_dir, "main", "feature/x"
        )
        prompt, options, role = run_query.await_args.args
        self.assertIn("diff text", prompt)
        self.assertEqual(role, "Reviewer")
        self.assertEqual(
            options.allowed_tools, ["Read", "Grep", "Glob", "Bash", "Write"]
        )
        self.assertIn("'main'", options.system_prompt)
        self.assertIn("'feature/x'", options.system_prompt)
```

(`self.context.project_dir` is `Path.cwd()` per `ReviewerAgentTests.setUp` already shown in the file.)

Add a small direct test for `_branch_diff` using a real repo, near the bottom of `test_agents.py` or in a new `class BranchDiffTests`:

```python
class BranchDiffTests(unittest.TestCase):
    def test_diff_includes_committed_and_uncommitted_changes_since_merge_base(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
            )
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            (root / "f.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
            subprocess.run(["git", "branch", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "checkout", "-q", "-b", "feature/x"], cwd=root, check=True
            )
            (root / "f.txt").write_text("committed change\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-q", "-am", "committed"], cwd=root, check=True)
            (root / "f.txt").write_text("committed change\nuncommitted too\n", encoding="utf-8")

            diff = _branch_diff(root, "main", "feature/x")

            self.assertIn("committed change", diff)
            self.assertIn("uncommitted too", diff)

    def test_raises_a_clear_error_when_target_does_not_exist(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
            )
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            (root / "f.txt").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

            with self.assertRaisesRegex(RuntimeError, "merge base"):
                _branch_diff(root, "does-not-exist", "master")
```

Add `_branch_diff` to `test_agents.py`'s `from meow.agents.reviewer import ...` line.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_prompts tests.test_agents -v`
Expected: FAIL — `ImportError`/`AttributeError` for `branch_review_prompt`/`review_branch`/`_branch_diff`.

- [ ] **Step 3: Implement `branch_review_prompt` in `src/meow/prompts.py`**

Add after `mr_review_prompt`:

```python
def branch_review_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the reviewer's inputs
    target: str,
    branch: str,
    review_file: Path,
    lint_commands: list[LintCommand],
    *,
    check_worktree_hygiene: bool,
) -> str:
    return (
        "You are a skeptical QA reviewer. You did not write this code "
        f"-- grade it critically. You are reviewing local branch {branch!r} "
        f"against its target branch {target!r}. There is no Sprint "
        "Contract; the diff given in the task message (between "
        f"{target!r} and {branch!r}) sets the scope, but unlike a GitLab "
        "merge request review, this branch is actually checked out here "
        "-- use Read/Grep/Glob/Bash to inspect the real code and run the "
        "project's own checks, not just the diff text. Mark each distinct "
        "concern PASS or FAIL with concrete evidence (a quoted diff hunk "
        "or file:line). "
        + lint_instructions(lint_commands)
        + " "
        + verification_instructions()
        + architecture_review_instructions(
            check_worktree_hygiene=check_worktree_hygiene
        )
        + _verdict_format(review_file, "concern")
    )
```

- [ ] **Step 4: Implement in `src/meow/agents/reviewer.py`**

Add the import (extend the existing `from meow.prompts import (...)` block with `branch_review_prompt`). Add the constant next to the other two:

```python
BRANCH_REVIEW_FILENAME = "branch-review.md"
```

Add `_branch_diff` after `_git_review_context`:

```python
def _branch_diff(active_dir: Path, target: str, branch: str) -> str:
    """`git diff` from `target`/`branch`'s merge-base to the current working
    tree -- includes both `branch`'s own commits since it diverged AND any
    uncommitted edits sitting in `active_dir` (a `ReviewFixAgent` round's
    fixes), so re-reviewing after a fix round sees it without requiring a
    commit. Mirrors `_git_review_context`'s single-sided `git diff` (base
    vs. working tree), just with a computed merge-base as the base instead
    of the implicit HEAD."""
    git = shutil.which("git")
    if not git:
        return ""
    merge_base = subprocess.run(
        [git, "-C", str(active_dir), "merge-base", target, branch],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    if merge_base.returncode != 0:
        raise RuntimeError(
            f"Could not find a merge base between {target!r} and "
            f"{branch!r} in {active_dir}: {merge_base.stderr.strip()}"
        )
    diff = subprocess.run(
        [git, "-C", str(active_dir), "diff", merge_base.stdout.strip()],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    return diff.stdout.strip()
```

Add `review_branch` to `ReviewerAgent`, after `review_merge_request`:

```python
    async def review_branch(self, target: str, branch: str) -> tuple[str, str]:
        """Grade a local branch's diff against a target branch, PASS/FAIL.

        Unlike `review_merge_request` (a remote diff, no local checkout),
        `branch` is actually checked out in the active working directory,
        so lint commands and Read/Grep/Glob/Bash access apply the same way
        `review_plan`/`review_prompt` do.
        """
        review_dir = self.context.active_working_dir() / self.context.config["docs_dir"]
        review_dir.mkdir(parents=True, exist_ok=True)
        review_file = review_dir / BRANCH_REVIEW_FILENAME
        diff_text = _branch_diff(self.context.active_working_dir(), target, branch)
        options = self.options(
            system_prompt=branch_review_prompt(
                target,
                branch,
                review_file,
                self.context.lint_commands(),
                check_worktree_hygiene=self.context.use_worktree,
            ),
            allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
            role="reviewer",
            skills=["superpowers:verification-before-completion"],
        )
        query_prompt = (
            f"Diff of branch {branch!r} against target {target!r} "
            f"(git diff {target}...{branch}, including any uncommitted "
            "changes):\n\n" + (diff_text or "(no diff -- branch matches target)")
        )
        await self.run_query(query_prompt, options, "Reviewer")
        verdict_text = review_file.read_text(encoding="utf-8")
        return _verdict_status(verdict_text), verdict_text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_prompts tests.test_agents -v`
Expected: PASS (all tests, including new ones)

- [ ] **Step 6: Commit**

```bash
git add src/meow/prompts.py src/meow/agents/reviewer.py tests/test_prompts.py tests/test_agents.py
git commit -m "Add ReviewerAgent.review_branch for local branch-vs-target diffs"
```

---

### Task 4: Generalize the review-fix round loop; wire in the "branch" review flavor

**Files:**
- Modify: `src/meow/orchestrator.py:228-263` (`_run_prompt_fix_rounds`)
- Modify: `src/meow/review_fix_review.py` (its one call site, plus a new rejection branch)
- Modify: `src/meow/plan_files.py:36-50` (`_detect_review_flavor`)
- Test: `tests/test_review_fix.py` (extend)

**Interfaces:**
- Consumes: `ReviewFixAgent`, `ReviewerAgent` (unchanged).
- Produces: `_run_prompt_fix_rounds(context: ProjectContext, initial_verdict: tuple[str, str], *, re_review: Callable[[], Awaitable[tuple[str, str]]]) -> bool` — was `(context, prompt, initial_verdict)` with `review_prompt(prompt)` hardcoded inside the loop; now the caller supplies how to re-review each round, so `review_fix_review.py`'s prompt-flavor branch and the new `branch_reviewer.py` (Task 5) share one loop implementation.
- Produces: `_detect_review_flavor` returns `"branch"` for `BRANCH_REVIEW_FILENAME` (checked before the generic `-review.md` fallback, same as `"gitlab"`/`"prompt"` already are).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_review_fix.py`, in `DetectReviewFlavorTests`:

```python
    def test_branch_based_review_file(self):
        self.assertEqual(
            orchestrator._detect_review_flavor(Path("docs/branch-review.md")),
            "branch",
        )
```

Add to `RunReviewFixReviewTests`:

```python
    async def test_branch_based_raises_a_clear_error_before_any_agent_runs(self):
        config = {
            "models": {},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "branch-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")

            with (
                patch("meow.review_fix_review.load_config", return_value=config),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                self.assertRaisesRegex(RuntimeError, "meow branch-review"),
            ):
                await review_fix_review.run_review_fix_review(
                    working_dir, "Check it", review_file
                )

            mock_fixer_cls.assert_not_called()
            mock_generator_cls.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_review_fix -v`
Expected: FAIL — `test_branch_based_review_file` gets `"plan"` instead of `"branch"` (no `BRANCH_REVIEW_FILENAME` match yet); `test_branch_based_raises_a_clear_error_before_any_agent_runs` fails differently (tries to treat it as a plan review and raises `FileNotFoundError` about a missing `branch.md`, not the expected message).

- [ ] **Step 3: Implement**

In `src/meow/plan_files.py`, import `BRANCH_REVIEW_FILENAME` alongside the other two filenames, and add the check before the generic suffix fallback:

```python
from meow.agents.reviewer import (
    BRANCH_REVIEW_FILENAME,
    MR_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
)
```

```python
    if name == MR_REVIEW_FILENAME:
        return "gitlab"
    if name == PROMPT_REVIEW_FILENAME:
        return "prompt"
    if name == BRANCH_REVIEW_FILENAME:
        return "branch"
    if name.endswith("-review.md"):
        return "plan"
```

Also update `_latest_review_file`'s docstring/glob is unaffected (`*review.md` already matches `branch-review.md`).

In `src/meow/orchestrator.py`, add the typing import and change the signature/body:

```python
from collections.abc import Awaitable, Callable
```

```python
async def _run_prompt_fix_rounds(
    context: ProjectContext,
    initial_verdict: tuple[str, str],
    *,
    re_review: Callable[[], Awaitable[tuple[str, str]]],
) -> bool:
    """Like `_run_review_rounds`, but for a review with no plan file or
    Sprint Contract to hand a `GeneratorAgent` -- fixes with `ReviewFixAgent`
    (a generic "fix these review findings" session) and re-reviews with
    whatever `re_review` the caller supplies (a prompt-based re-review for
    `meow review-fix-review`'s prompt flavor, a branch-diff re-review for
    `meow branch-review`). `initial_verdict` is always required here
    (unlike `_run_review_rounds`, this has no "run a fresh review first"
    mode -- every caller already has one).
    """
    max_rounds = context.config["max_rounds"]
    status, verdict = initial_verdict
    if status == "PASS":
        return True

    async with ReviewFixAgent(context) as fixer:
        for round_num in range(2, max_rounds + 1):
            logger.info(
                "review_fix_round_started", round=round_num, max_rounds=max_rounds
            )
            await fixer.fix(verdict)

            status, verdict = await re_review()
            logger.info(
                "review_fix_round_finished",
                round=round_num,
                status=status,
                summary=_review_summary(verdict) or "",
            )
            if status == "PASS":
                return True

    return False
```

In `src/meow/review_fix_review.py`, update the prompt-flavor call site and add the branch rejection. The `if flavor == "gitlab": raise ...` block stays; add right after it:

```python
    if flavor == "branch":
        raise RuntimeError(
            f"{resolved_review_file} is a branch review -- `meow "
            "review-fix-review` can't resume it (it doesn't know the "
            "target branch to re-diff against). Run `meow branch-review "
            "<branch> --target <target>` again instead; it already loops "
            "review, fix, and re-review until it passes."
        )
```

And change:

```python
    else:
        context = ProjectContext(working_dir, config)
        passed = await _run_prompt_fix_rounds(
            context, prompt, initial_verdict=initial_verdict
        )
```

to:

```python
    else:
        context = ProjectContext(working_dir, config)
        passed = await _run_prompt_fix_rounds(
            context,
            initial_verdict,
            re_review=lambda: ReviewerAgent(context).review_prompt(prompt),
        )
```

(`ReviewerAgent` is already imported in `review_fix_review.py` via `from meow.agents.reviewer import _verdict_status` — extend that import to include `ReviewerAgent` too.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_review_fix -v`
Expected: PASS (all tests, including the 2 new ones). `test_prompt_based_uses_review_fix_agent` must still pass unmodified — confirm it does (the lambda still calls `review_prompt(prompt)` with the same argument the test asserts on).

- [ ] **Step 5: Run the full suite to check nothing else broke**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: OK (no regressions elsewhere — nothing else calls `_run_prompt_fix_rounds` or `_detect_review_flavor` directly per the codebase search done during planning).

- [ ] **Step 6: Commit**

```bash
git add src/meow/orchestrator.py src/meow/review_fix_review.py src/meow/plan_files.py tests/test_review_fix.py
git commit -m "Generalize the review-fix round loop; recognize branch-review.md"
```

---

### Task 5: `branch_reviewer.py` top-level flow + CLI wiring

**Files:**
- Create: `src/meow/branch_reviewer.py`
- Modify: `src/meow/cli.py`
- Test: `tests/test_branch_reviewer.py` (new), `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `_ensure_existing_branch_worktree`, `_require_branch_checked_out` (Task 1), `ProjectContext(..., use_worktree=...)` (Task 2), `ReviewerAgent.review_branch` (Task 3), `_run_prompt_fix_rounds(context, initial_verdict, *, re_review=...)` (Task 4).
- Produces: `run_branch_review(working_dir: Path, branch: str, target: str, *, use_worktree: bool = True) -> None` — raises `RuntimeError` if it doesn't pass within `max_rounds`, same ending shape as `run_review_fix_review`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_branch_reviewer.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import branch_reviewer


class RunBranchReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_worktree_mode_creates_worktree_and_reviews_it(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            worktree_dir = working_dir / ".worktrees" / "branch-review-feature-x"

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch(
                    "meow.branch_reviewer._ensure_existing_branch_worktree",
                    return_value=worktree_dir,
                ) as mock_ensure,
                patch(
                    "meow.branch_reviewer.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                result = await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main"
                )

            self.assertIsNone(result)
            mock_ensure.assert_called_once_with(
                working_dir, "branch-review-feature-x", "feature/x"
            )
            mock_review.assert_awaited_once_with("main", "feature/x")

    async def test_in_place_mode_requires_the_branch_to_be_checked_out(self):
        config = {
            "models": {"reviewer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch(
                    "meow.branch_reviewer._require_branch_checked_out",
                    side_effect=RuntimeError("wrong branch"),
                ) as mock_require,
                self.assertRaisesRegex(RuntimeError, "wrong branch"),
            ):
                await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main", use_worktree=False
                )

            mock_require.assert_called_once_with(working_dir, "feature/x")

    async def test_passing_on_round_one_never_starts_the_fix_loop(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch(
                    "meow.branch_reviewer._require_branch_checked_out",
                ),
                patch(
                    "meow.branch_reviewer.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
            ):
                await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main", use_worktree=False
                )

            mock_fixer_cls.assert_not_called()

    async def test_raises_after_max_rounds_still_failing(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch("meow.branch_reviewer._require_branch_checked_out"),
                patch(
                    "meow.branch_reviewer.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ),
                self.assertRaisesRegex(RuntimeError, "did not pass"),
            ):
                await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main", use_worktree=False
                )


if __name__ == "__main__":
    unittest.main()
```

Add to `tests/test_cli.py` (find the existing argparse-only test class, e.g. near `ResumeAtCliTests`, and add a sibling):

```python
class BranchReviewCliTests(unittest.TestCase):
    def test_target_is_required(self):
        parser = cli._build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["branch-review", "feature/x"])

    def test_parses_branch_target_and_no_worktree(self):
        parser = cli._build_arg_parser()
        args = parser.parse_args(
            ["branch-review", "feature/x", "--target", "main", "--no-worktree"]
        )
        self.assertEqual(args.branch, "feature/x")
        self.assertEqual(args.target, "main")
        self.assertTrue(args.no_worktree)

    def test_dispatches_to_run_branch_review(self):
        with patch("meow.cli.run_branch_review") as mock_run:
            args = cli._build_arg_parser().parse_args(
                ["branch-review", "feature/x", "--target", "main"]
            )
            cli._dispatch(args, Path("/project"), use_worktree=False)

        mock_run.assert_called_once_with(
            Path("/project"), "feature/x", "main", use_worktree=True
        )
```

(Check the file's existing `from meow import cli` / `from unittest.mock import patch` imports and reuse them; `cli._dispatch` calls `asyncio.run(...)` on the coroutine `run_branch_review` returns, so patching `meow.cli.run_branch_review` with a `MagicMock` — not `AsyncMock` — returning a real coroutine is what every other dispatch test in this file already does; check one existing dispatch test, e.g. for `gitlab-review`, and mirror its exact patching style instead of guessing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_branch_reviewer tests.test_cli.BranchReviewCliTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'meow.branch_reviewer'`; CLI tests fail with `argparse` "invalid choice: 'branch-review'".

- [ ] **Step 3: Implement `src/meow/branch_reviewer.py`**

```python
"""
meow/branch_reviewer.py

The `meow branch-review` flow: review a local branch's diff against a
target branch entirely locally -- no GitLab MCP, no MR link needed -- then
fix and re-review it until it passes, reusing
`orchestrator._run_prompt_fix_rounds`'s ReviewFixAgent loop the same way
`review_fix_review.py`'s prompt-based half does, except each round's
re-review recomputes the branch's diff against the target instead of the
working tree's diff against HEAD (see `agents.reviewer._branch_diff`).

Unlike `meow issue`, there is no Sprint Contract and nothing gets pushed.
Unlike `meow gitlab-review`, this is never read-only: it fixes what it
finds, either in an isolated worktree (default) or in place on the
caller's current checkout (`--no-worktree`, which requires `branch` to
already be checked out there).
"""

import re
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import BRANCH_REVIEW_FILENAME, ReviewerAgent
from meow.config import load_config
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import _run_prompt_fix_rounds
from meow.worktree import _ensure_existing_branch_worktree, _require_branch_checked_out

logger = get_logger(__name__)

_BRANCH_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(component: str) -> str:
    return _BRANCH_UNSAFE.sub("-", component).strip("-")


async def run_branch_review(
    working_dir: Path, branch: str, target: str, *, use_worktree: bool = True
) -> None:
    """Review `branch` against `target`, fix findings, and re-review until
    it passes or `max_rounds` is exhausted (then raises `RuntimeError`).
    """
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])

    if use_worktree:
        feature_name = f"branch-review-{_sanitize(branch)}"
        active_dir = _ensure_existing_branch_worktree(working_dir, feature_name, branch)
    else:
        active_dir = working_dir
        _require_branch_checked_out(active_dir, branch)

    context = ProjectContext(active_dir, config, use_worktree=use_worktree)

    logger.info(
        "branch_review_started", branch=branch, target=target, worktree=use_worktree
    )
    initial_verdict = await ReviewerAgent(context).review_branch(target, branch)
    logger.info(
        "branch_review_round_finished", round=1, status=initial_verdict[0]
    )

    passed = await _run_prompt_fix_rounds(
        context,
        initial_verdict,
        re_review=lambda: ReviewerAgent(context).review_branch(target, branch),
    )

    review_file = active_dir / config["docs_dir"] / BRANCH_REVIEW_FILENAME
    if passed:
        logger.info(
            "branch_review_complete", branch=branch, review_file=str(review_file)
        )
        return

    logger.error(
        "branch_review_did_not_pass", branch=branch, max_rounds=config["max_rounds"]
    )
    raise RuntimeError(
        f"branch-review of '{branch}' against '{target}' did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        f"forever. Inspect {review_file}."
    )
```

- [ ] **Step 4: Wire into `src/meow/cli.py`**

Add the import at the top, alongside the other flow imports:

```python
from meow.branch_reviewer import run_branch_review
```

Add a parser function, near `_add_gitlab_review_parser`:

```python
def _add_branch_review_parser(subparsers: argparse._SubParsersAction) -> None:
    branch_review_parser = subparsers.add_parser(
        "branch-review",
        help=(
            "Review a local branch's diff against a target branch, then "
            "fix and re-review until it passes -- no GitLab MCP or MR "
            "link needed."
        ),
    )
    branch_review_parser.add_argument("branch", help="Local branch to review and fix.")
    branch_review_parser.add_argument(
        "--target",
        required=True,
        help="Branch to diff against, e.g. main -- required, never guessed.",
    )
    branch_review_parser.add_argument(
        "--no-worktree", "--noworktree", "-n",
        dest="no_worktree",
        action="store_true",
        help=(
            "Fix in place on the current checkout instead of creating an "
            "isolated worktree. Requires 'branch' to already be checked "
            "out there."
        ),
    )
    _add_common_args(branch_review_parser)
```

Register it in `_build_arg_parser`, next to `_add_gitlab_review_parser(subparsers)`:

```python
    _add_branch_review_parser(subparsers)
```

Add the dispatch function, next to `_dispatch_gitlab_review`:

```python
def _dispatch_branch_review(args, working_dir: Path) -> None:
    asyncio.run(
        run_branch_review(
            working_dir, args.branch, args.target, use_worktree=not args.no_worktree
        )
    )
```

Add it to `_COMMAND_HANDLERS`:

```python
    "branch-review": _dispatch_branch_review,
```

Update `cli_main`'s `_boot_repo` call so the `.worktrees/` gitignore entry lands before `branch-review` creates one:

```python
    _boot_repo(
        working_dir,
        include_gitignore=(
            use_worktree
            or args.command == "issue"
            or (args.command == "branch-review" and not args.no_worktree)
        ),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_branch_reviewer tests.test_cli -v`
Expected: PASS (new tests, and no regressions in the rest of `test_cli.py`)

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: OK

- [ ] **Step 7: Commit**

```bash
git add src/meow/branch_reviewer.py src/meow/cli.py tests/test_branch_reviewer.py tests/test_cli.py
git commit -m "Add meow branch-review: review and fix a local branch against a target"
```

---

### Task 6: Native-mode support (`reviewer-branch` prompt role, `--existing-branch` prepare)

**Files:**
- Modify: `src/meow/native_prepare.py`
- Modify: `src/meow/native_prompt.py`
- Modify: `src/meow/native_cli.py`
- Test: `tests/test_native.py` (extend), `tests/test_native_cli.py` (extend)

**Interfaces:**
- Produces (`native_prepare.py`): `PrepareOptions.existing_branch: str | None = None` field; `_resolve_active_dir` creates/reuses a worktree via `_ensure_existing_branch_worktree` when set (requires `options.name`), or, in `--no-worktree` mode, calls `_require_branch_checked_out(working_dir, options.existing_branch)` before returning.
- Produces (`native_prompt.py`): `"reviewer-branch"` added to `PROMPT_ROLES`; `_branch_review(context, target, branch) -> dict` helper (mirrors `_mr_review`); `role_prompt(..., target: str | None = None, branch: str | None = None)` dispatches to it when `role == "reviewer-branch"`; the `rules_key` regex extended to strip a trailing `-branch` too.
- Produces (`native_cli.py`): `prepare --existing-branch BRANCH` and `prompt --target T --branch B` (role-scoped, only meaningful for `reviewer-branch`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_native.py`, in `PrepareTests`:

```python
    def test_existing_branch_worktree_requires_a_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaises(ValueError):
                native.prepare(root, native.PrepareOptions(existing_branch="feature/x"))

    def test_existing_branch_worktree_is_created_on_the_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            result = native.prepare(
                root,
                native.PrepareOptions(name="br-feature-x", existing_branch="feature/x"),
            )

            head = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=result["active_dir"], capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(head, "feature/x")

    def test_existing_branch_that_does_not_exist_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaises(RuntimeError):
                native.prepare(
                    root,
                    native.PrepareOptions(name="br-nope", existing_branch="nope"),
                )

    def test_no_worktree_existing_branch_requires_it_checked_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            with self.assertRaises(RuntimeError):
                native.prepare(
                    root,
                    native.PrepareOptions(
                        use_worktree=False,
                        require_clean=False,
                        existing_branch="feature/x",
                    ),
                )
```

Add a new test class to `tests/test_native.py`:

```python
class BranchReviewPromptTests(unittest.TestCase):
    def test_reviewer_branch_prompt_names_target_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "main")

            result = native.role_prompt(
                root, root, "reviewer-branch", target="main", branch="feature/x"
            )

            self.assertIn("'main'", result["system_prompt"])
            self.assertIn("'feature/x'", result["system_prompt"])
            self.assertTrue(result["review_file"].endswith("branch-review.md"))
```

Add to `tests/test_native_cli.py` (mirror its existing prepare/prompt tests' style):

```python
    def test_prepare_existing_branch_flag_is_wired(self):
        result = subprocess.run(
            [sys.executable, "-m", "meow.cli", "native", "prepare", "--help"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn("--existing-branch", result.stdout)

    def test_prompt_target_and_branch_flags_are_wired(self):
        result = subprocess.run(
            [sys.executable, "-m", "meow.cli", "native", "prompt", "--help"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn("--target", result.stdout)
        self.assertIn("--branch", result.stdout)
```

(Check `test_native_cli.py`'s existing style first — it may invoke the parser in-process rather than via subprocess; if so, mirror whatever pattern its other `NativeCliTests` already use instead of introducing a subprocess call. Read a couple of its existing tests before writing these two.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_native tests.test_native_cli -v`
Expected: FAIL — `TypeError` on the unexpected `existing_branch`/`target`/`branch` kwargs, and the `--existing-branch`/`--target`/`--branch` flags missing from `--help` output.

- [ ] **Step 3: Implement in `src/meow/native_prepare.py`**

Extend the import and dataclass:

```python
from meow.worktree import (
    _boot_repo,
    _ensure_branch_worktree,
    _ensure_clean_tree,
    _ensure_existing_branch_worktree,
    _require_branch_checked_out,
    _resolve_working_dir,
)
```

```python
@dataclass(frozen=True)
class PrepareOptions:
    """What `prepare` needs beyond the project directory."""

    name: str | None = None
    use_worktree: bool = True
    source_branch: str | None = None
    branch: str | None = None
    existing_branch: str | None = None
    require_clean: bool = True
```

Update `_resolve_active_dir`:

```python
def _resolve_active_dir(
    working_dir: Path, options: PrepareOptions
) -> tuple[Path, bool]:
    """Pick the directory to work in: a branch worktree (issue flow), an
    existing-branch worktree (branch-review flow), a feature worktree
    (run/plan), or the project itself."""
    if options.branch:
        if not options.name:
            raise ValueError("--name is required together with --branch")
        return _ensure_branch_worktree(working_dir, options.name, options.branch), True
    if options.existing_branch:
        if not options.use_worktree:
            _require_branch_checked_out(working_dir, options.existing_branch)
            return working_dir, False
        if not options.name:
            raise ValueError("--name is required together with --existing-branch")
        return (
            _ensure_existing_branch_worktree(
                working_dir, options.name, options.existing_branch
            ),
            True,
        )
    active_dir, _, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=options.use_worktree,
        feature_name=options.name,
        source_branch=options.source_branch,
    )
    return active_dir, is_worktree
```

- [ ] **Step 4: Implement in `src/meow/native_prompt.py`**

Extend the import block:

```python
from meow.agents.reviewer import (
    BRANCH_REVIEW_FILENAME,
    MR_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
    _branch_diff,
    _git_review_context,
)
```

```python
from meow.prompts import (
    branch_review_prompt,
    explorer_prompt,
    generator_prompt,
    lint_fixer_prompt,
    mr_review_prompt,
    plan_review_prompt,
    planner_prompt,
    prompt_review_prompt,
    review_fixer_prompt,
)
```

```python
PROMPT_ROLES = (
    "planner",
    "generator",
    "explorer",
    "reviewer-plan",
    "reviewer-prompt",
    "reviewer-mr",
    "reviewer-branch",
    "review-fixer",
    "lint-fixer",
)
```

Add `_branch_review` after `_mr_review`:

```python
def _branch_review(context: ProjectContext, target: str, branch: str) -> dict:
    review_file = (
        context.active_working_dir() / context.config["docs_dir"] / BRANCH_REVIEW_FILENAME
    )
    diff_text = _branch_diff(context.active_working_dir(), target, branch)
    text = branch_review_prompt(
        target,
        branch,
        review_file,
        context.lint_commands(),
        check_worktree_hygiene=context.use_worktree,
    )
    query = (
        f"Diff of branch {branch!r} against target {target!r} (git diff "
        f"{target}...{branch}, including any uncommitted changes):\n\n"
        + (diff_text or "(no diff -- branch matches target)")
    )
    return {"system_prompt": text, "query": query, "review_file": str(review_file)}
```

Update `role_prompt`'s signature and dispatch:

```python
def role_prompt(  # ruff: ignore[too-many-arguments] -- mirrors the `meow native prompt` flags one to one
    working_dir: Path,
    active_dir: Path,
    role: str,
    *,
    plan_file: Path | None = None,
    focus: str | None = None,
    use_worktree: bool = False,
    target: str | None = None,
    branch: str | None = None,
) -> dict:
    """The exact system prompt (and task message, where there is one) the SDK
    agent for `role` would use, including project rules, so a Task subagent
    launched with it behaves like the SDK role."""
    if role not in PROMPT_ROLES:
        raise ValueError(f"role must be one of {PROMPT_ROLES}, got {role!r}")
    config = load_config(working_dir)
    context = ProjectContext(active_dir, config, use_worktree=use_worktree)
    if role == "reviewer-plan":
        if plan_file is None:
            raise ValueError("the reviewer-plan prompt needs --plan")
        result = _plan_review(context, plan_file, focus)
    elif role == "reviewer-prompt":
        result = _prompt_review(context, focus)
    elif role == "reviewer-mr":
        result = _mr_review(context)
    elif role == "reviewer-branch":
        if not target or not branch:
            raise ValueError("the reviewer-branch prompt needs --target and --branch")
        result = _branch_review(context, target, branch)
    else:
        result = _simple_prompt(role, context, plan_file)
    rules_key = re.sub(r"-(plan|prompt|mr|branch)$", "", role).replace("-", "_")
    prompt = result["system_prompt"]
    result["system_prompt"] = _with_rules(prompt, active_dir, rules_key)
    result["model"] = config["models"].get(rules_key)
    return result
```

(Note: `context.use_worktree = use_worktree` used to be a separate statement after construction; Task 2 lets it be passed straight into the constructor, so drop the old assignment line.)

- [ ] **Step 5: Implement in `src/meow/native_cli.py`**

In `_add_prepare`, add after the existing `--branch` argument:

```python
    parser.add_argument(
        "--existing-branch", dest="existing_branch", default=None,
        help=(
            "Check out this EXISTING branch (local or origin's) into a "
            "worktree, or (with --no-worktree) require it already checked "
            "out in place -- branch-review flow. Errors if it doesn't "
            "exist anywhere, unlike --branch."
        ),
    )
```

In `_prepare`, pass it through:

```python
def _prepare(args, working_dir: Path, _active: Path) -> dict:
    options = native.PrepareOptions(
        name=args.name,
        use_worktree=not args.no_worktree,
        source_branch=args.source_branch,
        branch=args.branch,
        existing_branch=args.existing_branch,
        require_clean=not args.allow_dirty,
    )
    return native.prepare(working_dir, options)
```

In `_add_prompt`, add after the existing `--focus`/`--worktree` arguments:

```python
    parser.add_argument(
        "--target", default=None, help="Target branch (reviewer-branch role).",
    )
    parser.add_argument(
        "--branch", default=None, help="Branch under review (reviewer-branch role).",
    )
```

In `_prompt`, pass them through:

```python
def _prompt(args, working_dir: Path, active: Path) -> dict:
    return native.role_prompt(
        working_dir,
        active,
        args.role,
        plan_file=_resolve(args.plan, active),
        focus=args.focus,
        use_worktree=args.worktree,
        target=args.target,
        branch=args.branch,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_native tests.test_native_cli -v`
Expected: PASS (all tests, including new ones)

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: OK

- [ ] **Step 8: Commit**

```bash
git add src/meow/native_prepare.py src/meow/native_prompt.py src/meow/native_cli.py tests/test_native.py tests/test_native_cli.py
git commit -m "Add native-mode support for branch-review: reviewer-branch role, --existing-branch"
```

---

### Task 7: `skills/branch-review/SKILL.md`

**Files:**
- Create: `skills/branch-review/SKILL.md`
- Modify: `tests/test_native_skills.py:13-22` (`CLI_FALLBACKS`)

**Interfaces:**
- Produces: a skill directory matching every other skill's dual-mode structure, discoverable as `/meow:branch-review` when this repo is installed as a Claude Code plugin.

- [ ] **Step 1: Write the failing test change**

In `tests/test_native_skills.py`, add to `CLI_FALLBACKS`:

```python
CLI_FALLBACKS = {
    "sprint": "meow run",
    "meow-plan": "meow plan",
    "meow-review": "meow review",
    "meow-cr": "meow cr",
    "review-fix-review": "meow review-fix-review",
    "meow-issue": "meow issue",
    "gitlab-review": "meow gitlab-review",
    "lint-fix": "meow lint-fix --report-only",
    "branch-review": "meow branch-review",
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_native_skills -v`
Expected: FAIL — `test_every_expected_skill_exists_with_matching_frontmatter` and `test_each_skill_keeps_native_and_cli_sections` fail with `FileNotFoundError` (`skills/branch-review/SKILL.md` doesn't exist yet); `test_no_unexpected_skill_directories` currently passes but would fail once Step 3 creates the directory before this test file is updated — keep the two changes in the same commit.

- [ ] **Step 3: Write `skills/branch-review/SKILL.md`**

```markdown
---
name: branch-review
description: Review a local branch's diff against a target branch, then fix and re-review it in a loop until it passes. Runs natively in this Claude Code session by default. Use when the user wants a branch (their own feature branch, or someone else's PR/MR branch already fetched locally) checked and fixed against a target like main -- no GitLab MCP or MR link needed.
---

# branch-review

Reviews a local branch's diff against a target branch entirely locally (plain
`git diff`, no GitLab MCP, no MR link), then fixes what it finds and re-reviews,
looping up to `max_rounds` -- unlike `gitlab-review`, this always fixes, never
just reports. Runs **natively** by default. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) first. Use **CLI mode** (bottom) only if explicitly
asked.

Two ways to run it: in an **isolated worktree** (default -- leaves whatever the
user currently has checked out untouched), or **in place** on the current
checkout (only when the user asks for that, or the branch is already what's
checked out and they want it fixed right there).

## Native mode

1. The branch and target are whatever the user gave -- both required; never
   guess a target (don't assume `main`). The project needs a `.harness.toml`
   at its root.
2. Resolve the working directory:
   - Isolated worktree (default): `meow native prepare --existing-branch "<branch>"
     --name "branch-review-<sanitized-branch>" --allow-dirty --working-dir
     "<project-path>"`. Sanitize the branch name for `--name` the same way a
     feature name normally is (non `[A-Za-z0-9._-]` characters -> `-`).
   - In place (user asked for it): `meow native prepare --existing-branch
     "<branch>" --no-worktree --allow-dirty --working-dir "<project-path>"`.
     This fails clearly if `<branch>` isn't what's actually checked out there
     -- tell the user to check it out first, or drop back to worktree mode.
   Either way this also fails clearly if `<branch>` doesn't exist locally or
   as `origin/<branch>` -- report that and stop.
3. Anchor the round counter on the branch name itself (there's no plan file):
   `meow native round "<active_dir>/<docs_dir>/branch-review-anchor" --reset`,
   then `round` once more -- the first review is round 1.
4. Each round: `round <anchor>` (stop if exhausted) -> (round 2+) fix the
   previous round's findings as a scoped fixer, smallest edit per finding, no
   scope creep -> project-wide lint (`meow native lint --active-dir
   "<active_dir>"`, fix blocking findings) -> fresh reviewer subagent using
   `meow native prompt reviewer-branch --target "<target>" --branch "<branch>"
   --active-dir "<active_dir>" [--worktree]` (pass `--worktree` when step 2
   used the isolated-worktree path) -> `meow native verdict <review_file>`
   (`branch-review.md` in `docs_dir`).
5. Report PASS, or if exhausted, the review file's path and remaining
   findings. If isolated-worktree mode was used, remind the user their own
   checkout was left untouched and where the worktree lives.

## CLI mode

```bash
meow branch-review "<branch>" --target "<target-branch>" --working-dir "<project-path>"
```

Add `--no-worktree` to fix in place instead of creating an isolated worktree
(requires `<branch>` already checked out there). If `meow` isn't on PATH, tell
the user to install it (README: `pip install -e .` in a venv, or `pipx install
-e .`). Stream `[reviewer]`/`[review_fixer]` progress; report PASS or, after
`max_rounds`, the review file path (`branch-review.md` in `docs_dir`).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_native_skills -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite + ruff**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests`
Run: `ruff check .`
Expected: OK / All checks passed!

- [ ] **Step 6: Commit**

```bash
git add skills/branch-review/SKILL.md tests/test_native_skills.py
git commit -m "Add branch-review skill (native + CLI mode)"
```

---

### Task 8: Docs (README, AGENTS, GUIDE)

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `GUIDE.md`

No tests (documentation only) — verified by re-reading the rendered sections.

- [ ] **Step 1: `README.md`**

Add a new `### meow branch-review` subsection after the existing `### meow gitlab-review` section (before `### meow lint-fix`):

```markdown
### `meow branch-review`

Reviews a local branch's diff against a target branch entirely locally — plain
`git diff`, no GitLab MCP, no MR link — then fixes what it finds and
re-reviews, looping up to `max_rounds`. Unlike `meow gitlab-review`, it always
fixes; unlike `meow issue`, there's no Sprint Contract and nothing gets pushed:

```bash
meow branch-review "feature/add-csv-export" --target main
```

`--target` is required — never guessed — since main/dev/master varies by
project. By default it checks out `branch` into its own isolated worktree
(reusing it on a repeated run against the same branch, like `meow issue`'s
worktree), leaving whatever's currently checked out untouched. `--no-worktree`
fixes in place instead, on whatever's already checked out — which must already
be `branch`, or it fails with a clear error rather than fixing the wrong code.
Writes its verdict to `branch-review.md` in `docs_dir`. A leftover
`branch-review.md` is rejected by `meow review-fix-review` (it has no target
branch to re-diff against) — rerun `meow branch-review` instead, which already
loops to `max_rounds` on its own.
```

Add a row to the plugin skills table:

```markdown
| `/meow:branch-review "<branch>" --target "<target>"` | `meow branch-review "<branch>" --target "<target>"` |
```

(placed after the `review-fix-review` row, matching CLI ordering.)

Add to the Layout tree, in the skills list:

```
│   ├── review-fix-review/SKILL.md       # /meow:review-fix-review -> harness review-fix-review
│   └── branch-review/SKILL.md           # /meow:branch-review -> harness branch-review
```

(change the previous `review-fix-review` line's `└──` to `├──` since it's no longer last)

and in the `src/meow/` file list, after `gitlab_reviewer.py`:

```
        ├── gitlab_reviewer.py          # `meow gitlab-review` flow: fetch MR, grade its diff
        ├── branch_reviewer.py          # `meow branch-review` flow: local branch-vs-target diff, fix loop
```

- [ ] **Step 2: `AGENTS.md`**

Add a paragraph after the existing `meow gitlab-review` paragraph (before `meow lint-fix`):

```markdown
`meow branch-review <branch> --target <target-branch>` reviews a local
branch's diff against a target branch (plain `git diff`, no GitLab MCP or
MR link needed), fixes what it finds, and re-reviews, looping up to
`max_rounds` like everything else. `--target` is required. Worktree by
default (an existing-branch worktree, reused across repeated runs);
`--no-worktree` fixes in place and requires `branch` already checked out
there.
```

Also add `branch-review` to the "Claude Code plugin" paragraph's skill list and to the `native_cli.py`/`native_prompt.py`/`worktree.py` descriptions in the `## Layout` paragraph if their one-line summaries need updating (check whether those files' docstrings changed meaningfully in Tasks 1/6 — if `worktree.py`'s AGENTS.md summary already just says "git/filesystem bootstrapping: .gitignore upkeep and creating per-feature worktrees", extend it to mention "and checking out an existing branch's worktree").

- [ ] **Step 3: `GUIDE.md`**

Update the §7 Troubleshooting line that lists `meow issue`, `meow gitlab-review`, `meow review-fix-review`'s own failure modes:

```markdown
`meow issue`, `meow gitlab-review`, `meow branch-review`, and `meow
review-fix-review` have their own failure modes (missing MCP config,
unreachable server, no MR checkout, wrong branch checked out, `max_rounds`
exhausted, etc.) — the full error-message reference lives in
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
```

- [ ] **Step 4: Re-read all three changed sections for accuracy**

Confirm every command example, flag name, and file path matches what Tasks 1–7 actually built (not what was originally planned, if anything drifted during implementation).

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md GUIDE.md
git commit -m "Document meow branch-review in README, AGENTS, and GUIDE"
```

---

### Task 9: Full verification and push

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests -v`
Expected: OK, with the pass count increased by roughly the number of new tests added across Tasks 1–7 (baseline before this plan: 216).

- [ ] **Step 2: Run ruff project-wide**

Run: `ruff check .`
Expected: `All checks passed!` — fix anything it flags (long lines, unused imports, complexity) before proceeding.

- [ ] **Step 3: Manually sanity-check the CLI help**

Run: `.venv/Scripts/meow.exe branch-review --help` (or `python -m meow.cli branch-review --help`)
Expected: shows `branch`, `--target` (required), `--no-worktree`, `--working-dir`.

- [ ] **Step 4: Verify local `main` is still in sync with `origin/main` before pushing**

Run: `git fetch origin && git rev-list --left-right --count main...origin/main`
Expected: `0	0` (no divergence since the encoding-fix push earlier this session).

- [ ] **Step 5: Push**

```bash
git push origin main
```

Report the final commit hash(es) and confirm the push succeeded (`git rev-list --left-right --count main...origin/main` → `0 0` again).

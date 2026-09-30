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

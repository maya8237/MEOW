---
name: gitlab-review
description: Fetch a GitLab merge request's diff through a configured GitLab MCP server and grade it, reporting a PASS/FAIL verdict. Use when the user wants meow to review a GitLab merge request by its URL, without checking it out or editing anything.
---

# gitlab-review

Runs `meow gitlab-review` against the current project: fetch one GitLab
merge request's title, description, and diff through a configured GitLab
MCP server, then grade it with meow's reviewer role and report PASS/FAIL
with evidence. This is read-only — like `/meow:meow-cr`, it never loops a
generator to fix issues, and unlike `/meow:meow-issue` it never creates a
worktree, edits code, or pushes anything.

1. The current project must have a `.harness.toml` at its root with
   `[gitlab.mcp]` set (`command`, and `args` if the server needs them) —
   see this repo's GUIDE.md §2. If it's missing, tell the user and point
   them at GUIDE.md rather than guessing at GitLab MCP settings.
2. The merge request is whatever URL the user gave when invoking this
   skill. If none was given, ask for the GitLab merge request URL first.
3. `meow gitlab-review` accepts `--working-dir PATH` (also `--work-dir` or
   `-d`) to select the project whose `.harness.toml` and `docs_dir` should
   be used. Use it whenever the project is not the current directory. Run,
   from the project root:

   ```bash
   meow gitlab-review "<merge-request-url>" --working-dir "<project-path>"
   ```

   If `meow` isn't found on PATH, tell the user to install it first (this
   repo's README: a venv with `pip install -e .`, or `pipx install -e .`
   for a global command) — don't guess at a path to some venv.
4. This prints progress as it goes (GitLab preflight/fetch lines, then
   `[reviewer]` lines) — stream that output to the user rather than waiting
   silently for it to finish.
5. Report the final outcome: PASS or FAIL, plus the review file's path
   (`gitlab-review.md` in the selected project's `docs_dir`) so the user can
   inspect the full evidence themselves. On failure before the review even
   starts (no `[gitlab]`/`[gitlab.mcp]` config, or no active GitLab MCP),
   report the error message verbatim; it already names the missing config.

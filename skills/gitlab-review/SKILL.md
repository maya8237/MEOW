---
name: gitlab-review
description: Fetch a GitLab merge request's diff and grade it, reporting a PASS/FAIL verdict. Runs natively in this Claude Code session by default, using the GitLab tools already connected. Use when the user wants meow to review a GitLab merge request by its URL, without checking it out or editing anything.
---

# gitlab-review

Read-only: fetch one merge request's title, description and diff, grade it with
meow's reviewer, report PASS/FAIL with evidence. It never edits code, creates a
worktree or pushes. Runs **natively** by default. Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) first. Use **CLI mode** (bottom) only if explicitly asked.

## Native mode

1. The merge request URL is whatever the user gave; if none, ask for it. The
   project needs a `.harness.toml` at its root. The `[gitlab.mcp]` table is
   **not** used: fetch through the GitLab MCP tools connected to this session
   (tool names containing `gitlab`). If none are connected, say so and stop.
2. Fetch title, description and the full diff of that merge request. Do not
   check anything out.
3. `meow native prepare --no-worktree --allow-dirty --working-dir "<project-path>"`
   (changes nothing; confirms config), then
   `meow native prompt reviewer-mr --working-dir "<project-path>"`. Dispatch one
   reviewer subagent per the shared protocol with `system_prompt` followed by this
   task message (the JSON's `query` is null for this role):

   ```
   Merge request title: <title>

   Merge request description:
   <description>

   Merge request diff:
   <diff>
   ```

   It must use only that material as its source of truth (Read/Grep/Glob for
   background only, no lint, no edits) and write `review_file` (`gitlab-review.md`).
4. `meow native verdict <review_file>`; report PASS or FAIL and the review file
   path. A missing GitLab connection is reported before any review starts.

## CLI mode

```bash
meow gitlab-review "<merge-request-url>" --working-dir "<project-path>"
```

Needs `[gitlab.mcp]` (`command`, and `args` if required) in `.harness.toml`; see
GUIDE.md §2. If `meow` isn't on PATH, tell the user to install it (README:
`pip install -e .` in a venv, or `pipx install -e .`). Stream its progress and
report PASS/FAIL plus the review file path (`gitlab-review.md` in `docs_dir`).

---
name: lint-fix
description: Run the current project's configured lint commands and fix whatever they report, using your own Edit/Bash tools. Use when the user wants lint issues found and fixed in the current session, not via a separate meow agent run.
---

# lint-fix

Runs `meow lint-fix --report-only` against the current project to get the
raw output of every command configured under `.harness.toml`'s `[[lint]]`
entries, then **you** — this Claude Code session — fix what it reports
yourself. This is the opposite division of labor from every other meow
skill here: `/meow:sprint`, `/meow:meow-review`, and friends have meow spin
up its own agents to do the work; this one has meow only run the linters
and hand you their raw output, because you are already in the session that
should be editing this code.

Do not run plain `meow lint-fix` (without `--report-only`) from this skill
— that mode spins up meow's own fixer agent and edits the project itself,
which duplicates what you're about to do and can race with your own edits.

1. The current project must have a `.harness.toml` at its root with at
   least one `[[lint]]` entry. If it's missing, tell the user and stop;
   point them at meow's `GUIDE.md` for onboarding rather than guessing at
   lint commands.
   `meow lint-fix` accepts `--working-dir PATH` (also `--work-dir` or `-d`)
   to target a project outside the current directory.
2. Run, from the project root:

   ```bash
   meow lint-fix --report-only --working-dir "<project-path>"
   ```

   If `meow` isn't found on PATH, tell the user to install it first (this
   repo's README: a venv with `pip install -e .`, or `pipx install -e .`
   for a global command) — don't guess at a path to some venv.
3. Read the command's output:
   - `Lint is clean -- no issues found.` and exit 0 — report that to the
     user and stop; there is nothing to fix.
   - A nonzero exit with one or more `$ <command>` blocks of raw lint
     output — that's every failure from every configured command, exactly
     as the linter reported it. Fix each one yourself in this working
     tree, the smallest edit that resolves it, without expanding scope
     into unrelated refactoring.
4. After fixing, re-run the same `meow lint-fix --report-only` command to
   confirm the tree is actually clean rather than assuming your edits
   worked. Repeat step 3 if anything is still reported.
5. Report the final outcome to the user: clean, or (if something couldn't
   reasonably be auto-resolved, e.g. a project-wide type error needing a
   design decision) what's still outstanding and why.

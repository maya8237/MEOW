---
name: lint
description: Run the current project's configured lint commands and fix whatever they report, using your own Edit/Bash tools. Use when the user wants lint issues found and fixed in the current session, not via a separate meow agent run.
---

# lint

Runs the project's `[[lint]]` commands and has **you** — this session — fix what
they report. This skill was already session-native; native mode now gets its
facts from `meow native lint` (same runner as the other skills). Read
[`../_shared/native-mode.md`](../_shared/native-mode.md) (relative to this
skill's base directory) for the helper's conventions. Never run plain
`meow run --lint-fix` here: that spins up meow's own fixer agent, duplicating
your work and racing your edits.

## Native mode

1. The project needs a `.harness.toml` with at least one `[[lint]]` entry;
   otherwise tell the user and stop (point at GUIDE.md).
2. Run `meow native prepare --no-worktree --allow-dirty --working-dir "<project-path>"`
   for `max_rounds` and `docs_dir`, then
   `meow native round <docs_dir>/lint-fix.md --reset`.
3. Auto-fix pass, then check: `meow native lint --fix --all-blocking --working-dir "<project-path>"`.
   `--all-blocking` fixes every configured command unconditionally, matching
   `meow run --lint-fix`'s own CLI behavior -- `gate` only controls what fails a
   *review*, not what this skill leaves alone.
   - `"clean": true` -> report that nothing is left and stop.
   - Otherwise each entry in `blocking` is a `$ <command>` block of raw linter
     output. Before editing, run `meow native prompt lint-fixer --active-dir
     <active_dir>` and follow its returned `system_prompt`, just as the CLI
     lint-fixer role follows `lint_fixer_prompt`. Fix each finding yourself
     with the smallest edit that resolves it, without unrelated refactoring;
     run `meow native lint --file <path> --active-dir <active_dir>` after each
     edit. Do not launch another agent.
4. Re-check with `meow native lint --all-blocking`; call
   `meow native round <docs_dir>/lint-fix.md` before each further fix attempt
   and stop if it says `exhausted`.
5. Report the final outcome: clean, or what is still outstanding and why (e.g. a
   project-wide type error needing a design decision).

## CLI mode

Report-only, then fix by hand:

```bash
meow run --lint-fix --report-only --working-dir "<project-path>"
```

Exit 0 with `Lint is clean -- no issues found.` means done; otherwise fix each
`$ <command>` block it printed and re-run to confirm. If `meow` isn't on PATH,
tell the user to install it (README: `pip install -e .` in a venv, or `pipx install -e .`).

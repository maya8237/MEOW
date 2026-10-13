# Task 1 report: validated UI session recipes

Implemented recipe validation and safe argv construction in
`src/meow/ui/sessions.py`, with deterministic tests in
`tests/ui/test_sessions.py`.

The validator generates UUID hex IDs server side, checks the selected repo,
validates recipe fields and option combinations, and parses CLI launches with
MEOW's real argument parser plus its run/review semantic validators. CLI
launches are limited to `run`, `plan`, and `review`; the UI server remains the
owner of the Python executable and process context. Claude capability checks
use only `claude --help` and `claude --version`. Default permissions use
`--permission-mode dontAsk --permission-prompts none`; bypass requires the
advertised pair of switches.

## TDD evidence

Red run before implementation:

```text
rtk proxy powershell -NoProfile -Command '$env:PYTHONPATH="src"; & "C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe" -m pytest tests/ui/test_sessions.py -q'
```

```text
ImportError while importing test module: cannot import name 'sessions' from 'meow.ui'
1 error during collection
```

Green focused run:

```text
rtk proxy powershell -NoProfile -Command '$env:PYTHONPATH="src"; & "C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe" -m pytest tests/ui/test_sessions.py -q'
20 passed in 2.47s
```

Ruff checks:

```text
rtk ruff check src/meow/ui/sessions.py tests/ui/test_sessions.py
passed
rtk ruff format --check src/meow/ui/sessions.py tests/ui/test_sessions.py
2 files already formatted
```

Coverage includes unattended run argv, command allowlisting, server-generated
IDs, NUL and dash-prefixed values, parser-backed unknown/duplicate/invalid
options, in-place opt-out semantics, review constraints, Claude default and
bypass argv, capability parsing, executable absence, and special-character
requests. No Claude session or paid/authenticated capability probe was run.

The controller reported the existing full-suite baseline as 1,236 passed,
7 skipped, and 104 subtests passed; this task did not rerun the full suite.

## Files

- `src/meow/ui/sessions.py` (new)
- `tests/ui/test_sessions.py` (new)
- `.superpowers/sdd/2026-10-13-ui-sessions/task-1-report.md` (new)

No parser source changes were needed. CLI semantic checks are reused from
`meow.cli.validation` and `meow.cli.review_cli`.

## Review round 1 fixes

Added regression tests before the fixes. The first focused run reproduced all
three findings:

```text
rtk proxy powershell -NoProfile -Command '$env:PYTHONPATH="src"; & "C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe" -m pytest tests/ui/test_sessions.py -q'
5 failed, 21 passed in 2.95s
```

The failures covered `no_worktree` silently disabling the default unattended
flag, Claude accepting unused CLI fields, and unresolved or arbitrary Claude
commands. The fixes keep unattended enabled by default regardless of
`no_worktree` (the parser now rejects the incompatible pair); permit an
in-place run only when `unattended: false` is explicit; reject workflow,
inputs, and arguments for Claude recipes; and accept only the explicit
`prompt` command or resolved `/meow:<builtin>` and
`/meow-custom:<selected-repository-skill>` slash commands. Arbitrary agent
names are rejected; Task 1 does not map MEOW TOML agent definitions to Claude
`--agent` identifiers.

Final focused checks:

```text
rtk proxy powershell -NoProfile -Command '$env:PYTHONPATH="src"; & "C:/Users/m/Documents/MEOW/.venv/Scripts/python.exe" -m pytest tests/ui/test_sessions.py -q'
28 passed in 2.96s
rtk ruff check src/meow/ui/sessions.py tests/ui/test_sessions.py
passed
rtk ruff format --check src/meow/ui/sessions.py tests/ui/test_sessions.py
2 files already formatted
```

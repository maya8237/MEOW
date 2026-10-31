# Tester setup

The AI tester loads your page in a real browser. Most projects need no tester
configuration: MEOW reads the setup the project already has. Check it any time with
`meow native verify --preflight`, for example:

```bash
meow native verify --work-dir . --no-lint --preflight
```

The preflight starts the dev server and a headless browser, so plain `native verify`
(which starts nothing) stays unchanged. Each check is `ok`, `fail` or `skipped`; a
`fail` carries a `fix` and, for config problems, a paste-ready `snippet`.

| Check | Proves |
|---|---|
| `target` | a `base_url`, `[[tester.dev_server]]` or `[tester.browser]` exists |
| `browser_config` | exactly one browser is selected; `browser_executable` is a file |
| `runtime`, `playwright` | node or bun is on PATH; `playwright` resolves |
| `ports` | MEOW can listen on and connect to a loopback port |
| `browser` | the selected browser launches headless |
| `page` | the page answers 2xx with visible content; servers are stopped after |

`/meow:test` and `/meow:onboard` run it and offer the fix; `--force-disable-tests`
skips every check.

## Minimal setup

Your own Chrome and a dev server in `frontend/` (MEOW picks the port):

```toml
[[tester.dev_server]]
cwd = "frontend"
command = "bun run dev --port {port}"
ready_url = "http://127.0.0.1:{port}/"

[tester]
browser_channel = "chrome"
browser_env = { PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true" }
```

## What MEOW infers

Read-only, never executed, never written to disk; explicit `[tester]` keys win per key.
`meow status`, `meow onboard` and `--preflight` show each value with its source.

- **Where:** the repo root, then its immediate subfolders holding a `package.json` or a
  Playwright config (`frontend/`, `web/`, `client/`, `app/`, `ui/` first; at most 10;
  never hidden, symlinked, `node_modules`, `dist` or `build`). One level only: for
  `apps/web` set `[[tester.dev_server]]` yourself. The first folder with a server wins.
- **`playwright.config.{ts,js,mjs,cjs}`:** literal `webServer` (object or array:
  `command`, `url` or `port`, `cwd`, `timeout`), `use.baseURL`, `channel`,
  `executablePath`. A config that names no browser selects Playwright's own.
- **`package.json`:** a `dev`/`start` script for Vite (`--port {port}`) or Next.js
  (`-p {port}`) under npm, pnpm, yarn or bun. Other frameworks are not guessed.
- Anything computed (env interpolation, variables) is reported as `could not read`
  and left to you. A server `cwd` escaping the repo is dropped with a note.

## Browser selection

For a frontend (`base_url` or a dev server) set exactly one; more than one is an error.

| Key | Meaning |
|---|---|
| `browser_channel` | `chrome`, `chrome-beta`, `chrome-dev`, `chrome-canary`, `msedge`, `msedge-beta`, `msedge-dev` |
| `browser_executable` | absolute path to a browser binary (checked by the preflight) |
| `browser_playwright` | `true`: Playwright's own Chromium (`bunx playwright install chromium`) |

`browser_env` sets Playwright variables for the tester; keys must match `PLAYWRIGHT_*`.
Playwright 1.63 has no env var for the browser path: use the keys above.
`PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS` only skips the missing-system-library
check; `PLAYWRIGHT_BROWSERS_PATH` relocates downloaded browsers. With no browser
selected the tester FAILs UI changes ("browser not configured") and each run prints a
warning; `browser_playwright = true` restores the previous behavior.

## Dev-server ports

`{port}` in `command`, `args`, `env` or `ready_url` (only as the port of the URL) makes
MEOW pick a free loopback port for every run, substitute it, and export `PORT`.
Pass it with your framework's flag; a server that ignores both times out with a hint.
A fixed port that is taken, or that MEOW cannot bind (privileged, reserved), is
relocated the same way. MEOW only binds `127.0.0.1`/`::1`.

On Windows, `WinError 10013`/`10048` or a refused port usually means a reserved range
or security software: run `netsh int ipv4 show excludedportrange protocol=tcp`.
Port errors are reported with their code and this hint, never as a traceback.

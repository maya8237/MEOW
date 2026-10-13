# MEOW UI

React, TypeScript, Vite, and shadcn/ui. The Python server owns the catalog and
creation APIs; React owns browsing, filters, details, and the creation dialog.
Repositories with matching names show the shortest parent path that distinguishes
them. Creation saves a starter file; the dialog and success notice direct users
to `/meow:customize` in Claude Code to write their workflow, agent, or skill contents.

## Development

Use Node.js 24 LTS and npm. From the repository root, run `meow ui --no-browser`.
In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints. API, logo, and font requests proxy to the Python
server on port 8765. For a different MEOW port, set `MEOW_UI_API` to its URL
before starting Vite (for example, `http://127.0.0.1:8767`). The development
proxy translates same-origin create requests to the Python server's origin;
foreign origins remain subject to the server's origin check.

```sh
npm test
npm run typecheck
npm run lint
npm run format:check
npm run build
```

The build writes `src/meow/ui/static/`. Commit those files with frontend
changes so Python installations can run `meow ui` without Node.js. Reload
the browser after rebuilding to pick up the new bundle. CI rebuilds and verifies
the committed bundle matches the sources. Frontend CI runs after the MEOW test
matrix and checks types, lint, formatting, interactions, and the build without
deploying anything. The wheel includes the bundle,
logo, and bundled JetBrains Mono fonts; the UI makes no CDN requests.

## Components

- `App.tsx`: catalog state, navigation, filters, polling, and save selection.
- `components/ItemDetail.tsx`: workflow steps, metadata, commands, and source.
- `components/CreateDialog.tsx`: validated creation and repo-specific sources.
- `components/CopyButton.tsx`: clipboard feedback and recovery.
- `components/ui/`: shadcn/ui primitives installed from its registry.
- `lib/catalog.ts`: API types and request helpers.
- `styles.css`: MEOW theme, layout, and responsive styles.

To add a shadcn component, run `npx shadcn@latest add COMPONENT` and use
`@/lib/utils` for the `cn` utility. Add only components used by the UI.

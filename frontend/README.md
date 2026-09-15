# PPSE Frontend — Manufacturing Control Tower

React 18 + TypeScript + Vite single-page app for the Production Priority & Scheduling Engine.
It answers the three questions from the specification: **what should we produce next**, **why**, and
**what happens if I change it**.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on http://localhost:5173 with `/api` proxied to `http://127.0.0.1:8000` (override with `VITE_DEV_PROXY_TARGET`) |
| `npm run build` | Type-check then produce `dist/` |
| `npm run typecheck` | `tsc --noEmit` (strict) |
| `npm run lint` | ESLint flat config (typescript-eslint, react-hooks, react-refresh) |
| `npm test` | Vitest (jsdom + Testing Library); `npm test -- --run` for CI |
| `npm run preview` | Serve the production build locally |
| `npm run build:demo` | Static **demo mode** bundle in `dist-demo/` (no backend needed, see below) |

Environment (see `.env.example`): `VITE_API_BASE_URL` (default `/api/v1`), `VITE_APP_ENV` (top-bar badge).

## Structure

```
src/
  main.tsx              entry; imports global styles
  app/                  App, providers (theme, query client, auth + API client, toasts), router, AppShell, RouteGuard, nav table
  api/                  client.ts (fetch wrapper), types/ (enums, models, results, config, api envelopes, system/sync —
                        mirrors of the OpenAPI components), one module per resource with typed functions + TanStack Query
                        hooks (orders, overrides, machines, priority, config, customers, alerts, audit, dataQuality, users,
                        auth, system, sync, schedule, analytics, simulation), queryKeys.ts
  components/           design-system pieces (DataTable, KpiCard, StatusPill, RiskBadge, ScoreBar, ExplanationPanel,
                        GanttChart, Timeline, FilterBar, states, PageHeader, Section, Toolbar, Modal, ConfirmDialog,
                        Toast, Tabs, ThemeToggle, AsyncContent) — each with its own CSS file
  pages/                one folder per screen (DESIGN_CONTRACT §11) + Login; pages/shared for reused column presets
  lib/                  formatters (INR lakh/crore, durations, %), time (UTC ↔ local), constants (tones, roles, factor
                        names), timeScale (Gantt geometry), chartTheme, useSearchState (URL-backed filters)
  styles/               tokens.css (dark-first industrial palette + light theme), base.css, utilities.css
  test/                 vitest setup and fixtures
```

## Conventions

* **Types mirror the backend** (`backend/app/domain/*.py`) with snake_case field names. Enriched read models
  (`OrderListItem`, `OrderDetail`, `MachineDetail`, `ScheduleView`) add optional computed fields so a lean API response still renders.
* **API access**: components never call `fetch`. `src/api/<resource>.ts` exposes `fetchX(client, ...)` functions and
  `useX(...)` hooks; `useApiClient()` supplies the client from context. List endpoints may return a bare array or a
  `{items, meta}` page — `unwrapList`/`toPaged` normalise both.
* **Auth**: JWT is held in memory and mirrored to `localStorage` (`ppse.auth`) for reload; `GET /auth/me` re-validates it.
  A 401 from any request logs the user out. Route access follows contract §9 (`RequireRole minRole readOnly`; the
  executive role reads every dashboard).
* **Design**: no UI framework. All colours/spacing come from CSS custom properties in `styles/tokens.css`; both
  themes are defined there (`data-theme` attribute or OS preference). Numbers use the monospace `.num` class. Status tones
  are semantic: `ready`, `running`, `blocked`, `late`, `at-risk`, `hold`, `neutral`, `done`.
* **Explainability**: `ExplanationPanel` renders the engine's `FactorScore`/`PriorityAdjustment` lists verbatim — the UI
  never composes its own reasons.
* **Business rules live in the backend** (`app/domain/config.py`); the UI only edits and displays them. Presentation
  thresholds (score bar colours) are in `lib/constants.ts`.
* Datetimes from the API are ISO-8601 UTC; `lib/time.ts` converts for display.

## Demo mode

`npm run build:demo` (`vite build --mode demo`, env from `.env.demo`: `VITE_DEMO_MODE=true`) produces `dist-demo/`, a
self-contained static bundle of the complete control tower that runs with **no backend**: an in-page demo backend
(`src/demo/`) answers every `/api/v1` call from real engine output captured once from the Python backend.

* **Data** — `src/demo/demo-data.json` (about 6 MB, loaded lazily as its own JS chunk, never fetched as JSON) holds the
  responses of the small synthetic plant (`--scale small`, seed 42): the plan history (v1 published, v2/v3 drafts, a
  rejected re-plan candidate), every order's detail / explanation / machine options, machines, analytics for every
  capacity dimension × period × horizon, one what-if preset per scenario kind, configuration versions, customers and
  rules, alerts, data-quality issues, the audit log, users, health, metrics and sync runs. Dates are shifted by whole
  weeks at load time so "today" always lies inside the plan horizon.
* **Behaviour** — `src/demo/transport.ts` exports `createDemoFetch()`, a drop-in `fetch` for the API client: it parses
  method, path, query and body, applies the bearer-token and role rules of `docs/API.md` (401 / 403 / 404 / 409 / 422
  with the same error envelope) and dispatches to handlers that keep the real API semantics. Mutations are **live**:
  expedite, hold / release, override priority, force next, move, lock machine, cancel overrides / expedites, schedule
  locks, configuration changes (every order is re-scored client-side from the captured factor scores; the weight
  preview reports the top-N moves), configuration version activation (rollback re-scores), customer rules, schedule
  generate / approve / publish / reject (real state machine, read-only writeback receipt), alerts acknowledgement,
  data-quality and sync runs, user management. Each one writes an audit row. The same transport is exposed as
  `window.__ppseDemo.fetch` (plus `runtime()` and `reset()`) for the browser console and end-to-end tests. Re-plan and what-if simulation are served
  from **captured presets** (the summary says so). The state lives in `localStorage`; "Reset demo data" (login page and
  System Administration) drops it.
* **Wiring** — when `VITE_DEMO_MODE=true` the API client uses the demo fetch, the router uses `createHashRouter` (deep
  links and refreshes work from any static path), `vite.config.ts` sets `base: "./"` and the top bar shows a DEMO
  badge. The login page lists the six seeded accounts with one-click sign-in and a short "what to try" list. The build
  also writes `dist-demo/artifact.html`: the built `index.html` without the doctype / html / head / body wrappers, for a
  host page that supplies its own skeleton (relative `assets/...` paths, `<div id="root">` kept).
* **Regenerating the data** (needs PostgreSQL and the backend virtualenv):

  ```
  cd ../backend && .venv/bin/python scripts/capture_demo_data.py --reset   # creates the ppse_demo database, migrates,
                                                                          # seeds, syncs the small plant, starts uvicorn
                                                                          # on :8010 and captures every response
  cd ../frontend && npm run build:demo && npx serve dist-demo               # or any static file server
  ```

  The capture is idempotent (`--reset` drops and recreates `ppse_demo`); it also writes
  `src/demo/.demo-verify.json` (git-ignored) with the derived views the demo recomputes, which
  `src/demo/derivations.test.ts` compares against when the file is present.

## Docker

`Dockerfile` builds the app and serves `dist/` with nginx. `nginx/default.conf.template` proxies `/api/` to
`$BACKEND_URL` (default `http://backend:8000`) and falls back to `index.html` for client-side routes.

```
docker build -t ppse-frontend .
docker run -e BACKEND_URL=http://host.docker.internal:8000 -p 8080:80 ppse-frontend
```

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

Environment (see `.env.example`): `VITE_API_BASE_URL` (default `/api/v1`), `VITE_APP_ENV` (top-bar badge).

## Structure

```
src/
  main.tsx              entry; imports global styles
  app/                  App, providers (theme, query client, auth + API client, toasts), router, AppShell, RouteGuard, nav table
  api/                  client.ts (fetch wrapper), types.ts (mirror of backend domain), one module per resource with typed
                        functions + TanStack Query hooks, queryKeys.ts
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

## Docker

`Dockerfile` builds the app and serves `dist/` with nginx. `nginx/default.conf.template` proxies `/api/` to
`$BACKEND_URL` (default `http://backend:8000`) and falls back to `index.html` for client-side routes.

```
docker build -t ppse-frontend .
docker run -e BACKEND_URL=http://host.docker.internal:8000 -p 8080:80 ppse-frontend
```

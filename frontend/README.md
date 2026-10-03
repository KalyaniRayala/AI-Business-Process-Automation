# Dashboard (frontend)

A React + TypeScript dashboard for the Business Process Automation Platform. It talks to the FastAPI backend in `../backend` and has no backend of its own.

| Page | Route | Backend endpoints |
|---|---|---|
| Overview | `/` | `/discovery/workflows`, `/automation/recommendations`, `/workflows` |
| Workflows (list, start a workflow) | `/workflows` | `GET /workflows`, `POST /workflows` |
| Workflow detail (status, answer, history, approval) | `/workflows/:id` | `GET /workflows/{id}`, `/workflows/{id}/audit`, `POST /workflows/{id}/decision` |
| Approvals queue | `/approvals` | `GET /workflows?status=awaiting_approval`, `GET /workflows/{id}`, `POST .../decision` |
| Workflow discovery | `/discovery` | `GET /discovery/workflows` |
| Automation recommendations | `/recommendations` | `GET /automation/recommendations` |
| Audit trail | `/audit` | `GET /audit-events` |
| Context graph | `/context` | `GET /context/processes/{key}`, `GET /context/nodes/{key}` |
| System (health and live activity) | `/system` | `GET /health/ready`, `GET /observability/summary` |

## Requirements

- Node.js 22.12 or newer (developed on Node 26) and npm.
- The backend running (see the root README), by default on `http://127.0.0.1:8000`.

## Run it

```bash
cd frontend
npm install          # first time only
npm run dev          # http://localhost:5173
```

Start the backend in another terminal (`cd backend`, then `uvicorn app.main:app --reload`).

**Use `http://localhost:5173`** rather than `127.0.0.1`: on some Windows machines Vite listens on IPv6 only.

### How the browser reaches the backend

The app calls the API under `/api`. Vite's dev and preview servers forward `/api/*` to the backend and strip the prefix (`vite.config.ts`), so the browser only talks to one origin and the backend needs no CORS configuration.

| Setting | Where | Default | Purpose |
|---|---|---|---|
| `VITE_BACKEND_URL` | shell, when starting Vite | `http://127.0.0.1:8000` | Backend the dev/preview proxy forwards to |
| `VITE_API_BASE_URL` | shell or `.env.local`, at build time | `/api` | Prefix the browser uses; set it to call a backend directly or to match a reverse proxy |

`VITE_*` values are **embedded in the built JavaScript and visible to every user**. Never put a secret in them. The dashboard needs none: the Cohere key and database URL stay in `backend/.env`.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server with hot reload and the `/api` proxy |
| `npm run build` | Type-check, then build to `dist/` |
| `npm run preview` | Serve the built app (with the same proxy) |
| `npm run typecheck` | TypeScript only |
| `npm test` | Run the test suite once (`npm run test:watch` to watch) |

For a deployment, `Dockerfile` builds `dist/` and serves it with nginx, which also forwards `/api` to the backend — the production counterpart of the dev proxy, so the backend still needs no CORS configuration. `docker compose up --build` from the project root runs the whole stack; see the root README. Serving `dist/` another way means routing `/api` yourself, or building with `VITE_API_BASE_URL` pointing at the backend and adding CORS there.

## Tests

Vitest with Testing Library, running in jsdom. The backend is **always mocked**: `src/test/mockApi.ts` replaces `fetch`, records every request and fails any test that calls an endpoint it did not mock. No test needs a running backend, the network or credentials.

## Structure

```
src/
  api/          typed client (client.ts) and response types (types.ts) mirroring the backend models
  components/   layout, approval panel, audit timeline, shared UI pieces
  hooks/        useAsync: loading with abort and stale-response protection
  pages/        one component per route
  utils/        date, duration and currency formatting
  test/         fetch mock, fixtures and render helpers
```

## Things to know

- **Approvals are final.** Approving runs the business action, so choosing a decision asks for confirmation first. The approver name is free text: the backend has no authentication yet, and neither does the dashboard.
- **Paused workflows need PostgreSQL to survive a restart.** With the default SQLite database the workflow engine keeps paused state in memory, so after a backend restart the queue can list a run that can no longer be resumed; opening it shows "not found". With PostgreSQL the state persists.
- **The System page shows one backend process.** Its counters reset when the backend restarts, and with several workers each refresh may answer from a different one. The approval count on that page comes from the database, so it is the exception.
- **Audit events are oldest first** (that is how the API orders them), so "Load more" continues forward in time.
- **Times** are shown in your local time zone; the backend stores UTC.
- **Dependencies are pinned** (`.npmrc` sets `save-exact`) and install scripts are disabled (`ignore-scripts`), so installing never runs package code.

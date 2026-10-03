# AI Business Process Automation Platform

## Goal
Enterprise AI platform that understands business processes and recommends/executes automation.

## Architecture
RAG → Tools → Structured Output → LangGraph → Human Approval
→ Business Action → PostgreSQL → Audit Trail → Context Graph
→ Enterprise Connectors → Workflow Discovery → Automation Recommendation
→ React → Evals → Observability → Docker → AWS

## Completed
- FastAPI
- Document ingestion + processing
- Cohere embeddings
- ChromaDB
- RAG
- Tools + tool calling
- Structured output
- LangGraph
- Human approval
- Business action
- PostgreSQL + checkpointer
- Audit Trail Steps 1–5
- Context Graph CG1
- Context Graph CG2
- Context Graph CG3
- Context Graph CG4
- Context Graph CG5 (graph context combined with RAG context before LLM generation)
- Context Graph CG6 (`context` node in the LangGraph workflow; graph facts shown to the
  approver and used in the final answer; the policy tool still decides approval)
- Enterprise Connectors (`app/services/connectors/`: connector interface, HTTP retry/auth
  layer, sync engine with audit events, first connector = Microsoft Entra ID; migration 0005
  adds the `connector` graph source type; run with `python -m app.services.connectors sync entra`)
- Workflow Discovery (`app/services/workflow_discovery_service.py`, `GET /discovery/workflows`:
  read-only analysis of audit events + business tables + the context graph's process model;
  observed paths, step stats, approval waits, volume, automation gaps; no new tables, no migration)
- Automation Recommendation (`app/services/automation_recommendation_service.py`,
  `GET /automation/recommendations`: deterministic, read-only, ranked and explained advice built on
  discovery + the policy rules; score = 100*(0.5*impact + 0.3*feasibility + 0.2*confidence);
  nothing executed or stored; no migration)
- Repository restructure: backend files moved into `backend/` (no code or path changes)
- React dashboard (`frontend/`: Vite + React 19 + TypeScript, pages for overview, workflows,
  approvals queue, discovery, recommendations, audit trail, context graph and system health; talks to the existing
  APIs through a dev proxy; one backend addition, read-only `GET /workflows` (status filter, paging,
  UTC timestamps); no migration)
- Evals (`app/evals/` harness + `evals/` datasets: five suites — extraction, policy, routing
  (deterministic, offline) and retrieval, grounding (need Cohere, replayed from a cassette or
  `--live`); substring/numeric/set scorers only, no LLM judge; per-suite thresholds gate the run and
  the CLI exits 1 on failure; `known_gap` cases record parsing weaknesses without blocking the gate;
  run with `python -m app.evals run`; no new tables, no migration; one supporting refactor:
  `approval_is_required` moved from `agent_graph.approval_node` into `tool_service`)
- Observability (`app/observability/`: correlation ids (`X-Request-ID`, plus `thread_id` for graph
  runs), structured logs with credential redaction (`LOG_FORMAT=text|json`), an in-process metrics
  registry rendering Prometheus text at `GET /metrics`, spans with recent-activity and recent-error
  ring buffers, HTTP middleware, readiness at `GET /health/ready` (503 when an essential check
  fails; never calls Cohere) and `GET /observability/summary` for the dashboard's `/system` page;
  model calls instrumented with `@measure_model_call`, workflow nodes by `WorkflowObserver` reading
  the same task stream as the audit observer; no new dependencies, no new tables, no migration)
- Docker (`backend/Dockerfile` two-stage, non-root, HEALTHCHECK on `/health/ready`;
  `frontend/Dockerfile` builds `dist/` and serves it with nginx (`frontend/nginx.conf`) which
  proxies `/api` to `backend:8000` — the production counterpart of the Vite dev proxy;
  `docker-compose.yml` runs postgres 18 + backend + frontend, health-gated startup, named volumes
  `pgdata` and `chroma`, database not published, backend on 127.0.0.1 only; secrets only from the
  environment via `${VAR:?}`, `.env.docker.example` → root `.env` (git-ignored);
  `tests/test_docker_config.py` asserts these properties without needing Docker.
  Docker 29.8.1; `docker compose config`, `build` and `up` all verified on this machine
  (every compose command needs the root `.env` first, by design). Images: bpa-backend 1.14GB,
  bpa-frontend 94MB. Live stack checked: all three containers healthy, dashboard on :8080,
  `/api/*` proxied, `X-Request-ID` passed through, SPA fallback and asset caching working,
  readiness fully `ok` (PostgresSaver durable, unlike the SQLite dev default).
  Two Docker-only fixes were needed: postgres 18+ requires the volume at
  `/var/lib/postgresql` (not `/var/lib/postgresql/data`), and the backend healthcheck needed
  timeout 15s / start-period 180s / start-interval 5s because first boot runs all migrations
  (~70s). `pyyaml` added to requirements-dev.txt for the config tests)

## Project layout
- `backend/` — the Python backend: `app/`, `tests/`, `evals/`, `migrations/`, `data/`, `chroma_db/`,
  `alembic.ini`, `pytest.ini`, `requirements*.txt`, `.env`, `.env.example`.
  Two `evals` folders, on purpose: `app/evals/` is the harness code, `evals/` is the data
  (datasets and cassettes) plus its own README.
- `frontend/` — the React dashboard (Vite + React + TypeScript). Node.js 22.12+ is required
  (installed system-wide on this machine; if `node` is not found in a shell started earlier, add
  the Node.js install folder to its PATH). Run from `frontend/`: `npm run dev` (open
  `http://localhost:5173`, not 127.0.0.1), `npm test`, `npm run typecheck`, `npm run build`.
  `.npmrc` pins exact versions and disables install scripts. In dev, Vite proxies `/api/*` to the
  backend (`VITE_BACKEND_URL`, default `http://127.0.0.1:8000`). `VITE_*` values are public: never put
  secrets in them.
- `docs/`, `README.md`, `CLAUDE.md`, `.gitignore`, `docker-compose.yml`,
  `.env.docker.example`, `.venv/` stay at the project root. Docker runs from the root
  (`docker compose up --build`), not from `backend/`.
- **Run every backend command from `backend/`** with the root venv, e.g.
  `cd backend && ../.venv/Scripts/python -m pytest`, `uvicorn app.main:app`,
  `alembic check`, `python -m app.services.connectors list`, `python -m app.evals run`. Imports stay `app.*`; `data/`,
  `chroma_db`, `.env` and the SQLite default resolve relative to `backend/`.

## Current Step
AWS — not started; requires separate approval.

## Important rules
- Existing project: never create a new project.
- Inspect before modifying.
- Implement only the approved step.
- Do not use Neo4j.
- PostgreSQL is the system of record.
- Do not modify completed layers unnecessarily.
- Run tests after changes.
- Do not commit unless explicitly asked.

## Current roadmap
AWS
# AI Business Process Automation Platform

An AI platform that understands a business process (currently **Finance → Expense Reimbursement**), retrieves the relevant company policy, checks enterprise data, pauses for **human approval** when policy requires it, and then executes the **business action**.

See [docs/business_problem.md](docs/business_problem.md) for the business context.

## Architecture

Target roadmap (✅ = implemented, 🟡 = partial, ⬜ = not started):

```
✅ Python + RAG → ✅ Tools → 🟡 Structured Output → ✅ LangGraph → ✅ Human Approval
→ ✅ Business Action → ✅ PostgreSQL → ✅ Audit Trail → ✅ Context Graph
→ ✅ Enterprise Connectors → ✅ Workflow Discovery → ✅ Automation Recommendation
→ ✅ React Dashboard → ✅ Evals → ✅ Observability → ✅ Docker → ⬜ AWS
```

### Workflow graph (`app/services/agent_graph.py`)

```
START → analyze ─┬─ employee ─┐
                 ├─ policy   ─┼─ synchronize → check_results ─┬─ recovery → END
                 └─ rag      ─┘                               └─ approval
                                                                   │
                         ┌─────────────── approved / not_required ─┤
                         ▼                                         ▼ rejected
                       action → final → END              rejection → final → END
```

- **employee** – looks up the employee ID (e.g. `E102`) in the relational database.
- **policy** – applies the expense thresholds: > $500 needs manager approval; > $5,000 needs manager and finance approval.
- **rag** – semantic search over ingested documents (Cohere embeddings + ChromaDB).
- **approval** – pauses the graph with LangGraph `interrupt()` until a human approves or rejects.
- **action** – saves the expense reimbursement to the database (at most once per workflow).
- **final** – generates the answer with the Cohere LLM from all gathered context.
- **recovery** – returns a structured failure if any lookup fails.

### Project layout

```
backend/                          Python backend: run backend commands from here
  app/
    main.py                          FastAPI app and workflow endpoints
    services/
      agent_graph.py                 LangGraph workflow + human approval
      business_action_service.py     Expense reimbursement action (persisted, idempotent)
      database_service.py            Migrations, seeding, employees, workflow runs, decisions
      db.py                          SQLAlchemy engine and sessions (DATABASE_URL)
      db_models.py                   SQLAlchemy table models
      checkpointer.py                LangGraph checkpointer (PostgresSaver or MemorySaver)
      audit_service.py               Audit trail: event types, write and query functions
      workflow_audit.py              Node-level audit observer (node_started/completed/failed)
      workflow_discovery_service.py  Workflow discovery: observed paths, step stats, process-model gaps
      automation_recommendation_service.py  Ranked, explained automation recommendations
      document_processor.py          PDF/TXT/DOCX/CSV loading, cleaning, chunking
      embedding_services.py          Cohere embeddings
      vector_store.py                ChromaDB storage and search
      rag_service.py                 Retrieval + answer generation
      llm_service.py                 Cohere chat client
      structured_output_service.py   JSON business-rule extraction
      tool_service.py                Tools + tool schemas
      tool_calling_service.py        LLM tool-calling agent
      tool_executor.py               Retry wrapper for tools
      connectors/                    Enterprise connectors (base, http, settings, entra, sync, CLI)
    evals/                           Eval harness: scorers, runner, report, suites
    observability/                   Logs, metrics, spans, HTTP middleware, health checks
    test_*.py                        Manual scripts (call the live Cohere API)
  migrations/                        Alembic schema migrations
  tests/                             Offline pytest suite
  evals/                             Eval datasets and cassettes (see backend/evals/README.md)
  data/                              Sample documents and SQLite database
  requirements.txt, requirements-dev.txt, alembic.ini, pytest.ini
  .env.example                     Copy to backend/.env (git-ignored)
  Dockerfile, .dockerignore          Backend image (multi-stage, non-root)
frontend/                          React dashboard (see frontend/README.md)
  Dockerfile, nginx.conf             Dashboard image: static build served by nginx, /api proxied
docs/                              Business problem description
docker-compose.yml                 The whole stack: database, API, dashboard
.env.docker.example                Copy to .env for Docker Compose (git-ignored)
.venv/                             Python virtual environment (project root)
```

## Implemented features

- Document ingestion for PDF, TXT, DOCX and CSV, with cleaning and word-based overlapping chunks
- Cohere `embed-v4.0` embeddings stored in a persistent ChromaDB collection (re-ingesting a document updates its chunks)
- RAG retrieval and grounded answer generation (Cohere `command-a-03-2025`)
- Tools: expense-policy lookup, employee lookup and document search, with JSON schemas and retries
- LLM tool calling (single round)
- Structured JSON business-rule extraction
- LangGraph workflow with parallel lookups, error recovery, and checkpointing
- Durable workflow state on PostgreSQL (`PostgresSaver`): workflows waiting for approval survive restarts and are shared across workers
- Audit trail: append-only, queryable record of workflow, approval, business-action and node events, with read APIs
- Human-in-the-loop approval via `interrupt()` / resume
- Business action execution: reimbursements saved with a transaction ID, idempotent per workflow
- REST API to start workflows, check status, and submit approval decisions
- SQLAlchemy database layer with Alembic migrations; PostgreSQL via `DATABASE_URL` (SQLite by default)
- Persistent records of workflow runs, approval decisions (with optional approver and comment) and reimbursements
- Database-enforced single decision per workflow (concurrent decisions get `409`)
- Offline pytest suite (no API calls)
- Evals: dataset-driven scoring of question parsing, approval rules, routing, retrieval recall and answer grounding, with a pass/fail gate and recorded model responses
- Observability: correlated request ids, structured logs with credential redaction, Prometheus metrics, readiness checks and a live System page in the dashboard
- Docker: one `docker compose up` for PostgreSQL, the API and the nginx-served dashboard, with health-gated startup, named volumes for state and no secrets in the images

## Prerequisites

(Not needed if you use [Docker](#run-it-with-docker).)

- Python 3.13
- Node.js 22.12+ and npm (only for the dashboard in `frontend/`)
- PostgreSQL 14+ (optional; SQLite is used when `DATABASE_URL` is not set)
- A [Cohere](https://cohere.com) API key

## Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in your own values. **Never commit `.env`.**

| Variable | Required | Description |
|---|---|---|
| `COHERE_API_KEY` | Yes | Cohere API key for the LLM and embeddings |
| `LOG_LEVEL` | No | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `LOG_FORMAT` | No | `text` (default) or `json`, one object per line for a log collector |
| `DATABASE_URL` | No | SQLAlchemy URL, e.g. `postgresql+psycopg://bpa_app:<password>@localhost:5432/bpa` (default `sqlite:///data/business.db`) |
| `TEST_DATABASE_URL` | No | Shell-only: runs `pytest` against a separate, disposable PostgreSQL database |
| `CHROMA_PATH` | No | ChromaDB directory (default `chroma_db`); used by tests for isolation |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | For the Entra connector | Application credentials for [Enterprise connectors](#enterprise-connectors) |
| `ENTRA_AUTHORITY_URL`, `ENTRA_GRAPH_URL` | No | Override the Microsoft login / Graph endpoints (https only) |
| `CONNECTOR_TIMEOUT_SECONDS`, `CONNECTOR_MAX_RETRIES`, `CONNECTOR_MAX_PAGES` | No | Connector HTTP timeout (15), retries (3) and page cap (100) |

## Run it with Docker

The quickest way to run the whole platform — PostgreSQL, the API and the dashboard — without installing Python, Node or PostgreSQL.

```bash
cp .env.docker.example .env     # then set COHERE_API_KEY and POSTGRES_PASSWORD
docker compose up --build
```

Then open **http://localhost:8080**.

| Service | Image | Published |
|---|---|---|
| `db` | `postgres:18-alpine` | nothing: only the backend reaches it |
| `backend` | built from `backend/Dockerfile` | `127.0.0.1:8000` for curl, `/docs` and a metrics scrape |
| `frontend` | built from `frontend/Dockerfile` (nginx) | `8080` — the dashboard |

The dashboard calls `/api` on its own origin and nginx forwards it to the backend, exactly as the Vite dev proxy does, so the backend needs no CORS configuration and only one port has to be published.

**Secrets come from the environment.** Compose reads `.env` in the project root (git-ignored). There is no default password: compose stops with a clear message if `POSTGRES_PASSWORD` or `COHERE_API_KEY` is unset, rather than starting with a password that would be the same on every machine. This applies to *every* compose command, including `docker compose config`, so copy the example file before running any of them. `backend/.env` is excluded from the image.

**State lives in volumes**, not in the images: `pgdata` for PostgreSQL and `chroma` for the vector store, so both survive `docker compose up --build`. Because the database is PostgreSQL, workflows paused for approval survive a restart.

Useful commands:

```bash
docker compose up --build -d          # start in the background
docker compose logs -f backend        # JSON logs, one object per line
docker compose ps                     # health of each container
docker compose exec backend python -m app.evals run
docker compose exec backend alembic current
docker compose down                   # stop; add -v to delete the volumes too
```

Ingesting documents inside the container writes to the `chroma` volume:

```bash
docker compose exec backend python -m app.services.document_processor
```

The image installs `requirements.txt`, not the dev requirements, so `pytest` is not available inside the container; run the test suite on the host as described under [Run tests](#run-tests).

Notes on the images: the backend is a two-stage build (the compiler toolchain stays out of the runtime image) running as an unprivileged user, with a `HEALTHCHECK` on `/health/ready` — so the dashboard container only starts once the API can actually serve. The frontend build runs the type-check, so a type error fails the image build. The frontend image carries only `dist/` and nginx.

## Installation

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r backend/requirements-dev.txt   # or backend/requirements.txt for runtime only
cp backend/.env.example backend/.env          # then add your Cohere key
```

The virtual environment lives at the project root. **Run every backend command below from `backend/`** (`cd backend`) with the environment activated: the app, the tests, Alembic and the sample data (`data/`, `chroma_db/`) are all resolved relative to it.

### Database and migrations

The schema is managed by Alembic (`migrations/`). The app applies pending migrations automatically at startup, then seeds the sample employees. You can also run them manually from `backend/`:

```bash
cd backend
alembic upgrade head                           # apply migrations
alembic current                                # show the applied revision
alembic check                                  # verify models and database match
alembic revision --autogenerate -m "message"   # create a new migration after changing db_models.py
```

An existing `data/business.db` created by earlier versions is upgraded in place: its employee rows are kept and the new tables are added.

Tables:

| Table | Written by | Contents |
|---|---|---|
| `employees` | startup seed | Employee directory |
| `workflow_runs` | API (start / decision) | One row per workflow: question, employee, amount, status, approval status, errors |
| `approval_decisions` | `POST /workflows/{id}/decision` | Decision, optional approver and comment, timestamp; unique per workflow |
| `reimbursements` | graph `action` node | Transaction ID, employee, amount, status; unique per workflow |
| `audit_events` | services, API and node observer | Append-only event history (see [Audit trail](#audit-trail)) |

### Use PostgreSQL

Create a database and an application role (run as the `postgres` superuser, e.g. with `psql -U postgres`; on Windows `psql` is in `C:\Program Files\PostgreSQL\<version>\bin`):

```sql
CREATE ROLE bpa_app LOGIN PASSWORD '<choose-a-password>';
CREATE DATABASE bpa OWNER bpa_app;
CREATE DATABASE bpa_test OWNER bpa_app;   -- optional, for tests
```

Then set `DATABASE_URL` in `.env` (never commit it):

```
DATABASE_URL=postgresql+psycopg://bpa_app:<password>@localhost:5432/bpa
```

Start the app (or run `alembic upgrade head`); the tables are created and employees are seeded automatically. SQLite data in `data/business.db` is not copied; it only contains the sample employees, which are re-seeded.

### Workflow state (checkpoints)

LangGraph saves each workflow's state after every step, which is how a paused workflow can be resumed later.

| `DATABASE_URL` | Checkpointer | Paused workflows after a restart |
|---|---|---|
| PostgreSQL | `PostgresSaver` | Kept, and shared across server workers |
| SQLite (default) | `MemorySaver` | Lost |

`PostgresSaver` creates and upgrades its own tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) at app startup. They are not part of the Alembic migrations, and Alembic is configured to ignore them.

### Ingest documents

Run commands from `backend/`. This loads `data/expense_process.txt` into ChromaDB (it calls the Cohere embeddings API):

```bash
python -m app.test_vector_store
```

## Run the FastAPI application

```bash
cd backend
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs
- Health check: `GET /health`

## Run tests

```bash
cd backend
pytest
```

The suite in `backend/tests/` runs fully offline. It uses a dummy API key, temporary SQLite and ChromaDB paths, and stubs the Cohere calls, so it never touches your real data or spends API credits.

To run the same suite against PostgreSQL, point `TEST_DATABASE_URL` at a **separate, disposable** database (the suite refuses to use the `DATABASE_URL` from `.env`):

```bash
# macOS / Linux
TEST_DATABASE_URL=postgresql+psycopg://bpa_app:<password>@localhost:5432/bpa_test pytest
# Windows PowerShell
$env:TEST_DATABASE_URL="postgresql+psycopg://bpa_app:<password>@localhost:5432/bpa_test"; pytest
```

The scripts in `app/test_*.py` are **manual** and call the live Cohere API. Run them individually from `backend/`, for example:

```bash
python -m app.test_rag
python -m app.test_agent_graph   # interactive approve/reject prompt
```

## Evals

The tests stub the LLM and the retriever, so they prove the workflow is wired correctly but say nothing about answer or retrieval quality. The evals score that against labelled datasets and fail the run when a score drops.

```bash
cd backend
python -m app.evals list      # suites and case counts
python -m app.evals run       # deterministic suites; exits 1 if the gate fails
python -m app.evals run --replay   # also the model suites, from recorded responses
python -m app.evals run --live     # also the model suites, calling Cohere
```

| Suite | Measures | Needs |
|---|---|---|
| `extraction` | Employee id and amount parsed from the question | nothing |
| `policy` | Approval thresholds and the gate they drive | nothing |
| `routing` | Recovery routing, and the path after a decision | the workflow graph |
| `retrieval` | Recall of the policy sentence an answer needs | Cohere + ingested documents |
| `grounding` | Answers stay in the context and refuse when it is silent | Cohere |

The model suites are skipped unless you pass `--replay` or `--live`, and a skip is not a failure. `--live` spends Cohere quota; `--live --record` saves the responses to `backend/evals/cassettes/` so later runs can replay them for free. No cassette is committed, so `--replay` reports every model case as skipped until you record one.

Datasets, thresholds, scoring and the documented known gaps in question parsing are described in [backend/evals/README.md](backend/evals/README.md).

## Observability

The evals say how well the platform decides; this says how it is running. Four endpoints, no extra dependencies and no external collector required:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness: the process is up |
| `GET /health/ready` | Readiness: the database, vector store and model provider are usable (`503` when an essential check fails) |
| `GET /metrics` | Prometheus text format: requests, workflow runs and nodes, model calls, errors |
| `GET /observability/summary` | A compact JSON view for the dashboard's **System** page |

**Correlation.** Every request gets an id, returned as `X-Request-ID` and attached to every log line written while it is handled. A client may supply its own id; anything long or odd is replaced. Workflow runs also carry their `thread_id`, so the logs of a node and the request that started it line up.

**Logs.** `LOG_FORMAT=json` writes one JSON object per line; `text` (the default) is readable in a terminal. Both redact credentials — a password inside a connection string, or anything that looks like `api_key=...` — before the line is written. The application's own request line (method, route, status, duration, request id) comes from the middleware; uvicorn's access log is configured separately and is not affected by `LOG_FORMAT`.

**What is measured.** HTTP requests (by route template, never the raw path), workflow runs and resumes by outcome, each workflow node's duration and status, every Cohere call, and errors by kind. Readiness never calls Cohere: it checks that the key is configured, because a probe that embeds text on every call is a bill, not a signal.

```bash
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/metrics
curl "http://127.0.0.1:8000/observability/summary?limit=10"
```

Metrics live in the process that recorded them and reset when it restarts, so a scrape returns one worker's view. The audit trail remains the durable record; this is the live one.

## Human approval API flow

**1. Start a workflow**

```http
POST /workflows
{"question": "For employee E102, process a $750 expense."}
```

Response (`201`) when approval is needed:

```json
{
  "thread_id": "3f6c...",
  "status": "awaiting_approval",
  "approval_request": {
    "type": "approval_required",
    "message": "This expense requires approval before the workflow can continue.",
    "employee": {"...": "..."},
    "policy": {"expense_amount": 750.0, "approval": "Manager approval required."}
  }
}
```

Expenses of $500 or less need no approval and complete immediately with `status: "success"`.

**2. Check status (optional)**

```http
GET /workflows/{thread_id}
```

To list runs, for example everything waiting for a decision (newest first, `limit` 1-200, `offset` for paging; the response also carries the total):

```http
GET /workflows?status=awaiting_approval&limit=50
```

`status` is one of `running`, `awaiting_approval`, `success`, `rejected`, `failed`.

**3. Approve or reject**

```http
POST /workflows/{thread_id}/decision
{"decision": "approve", "approver": "Priya", "comment": "Receipt verified."}
```

`approver` and `comment` are optional. The decision is saved before the workflow resumes.

Returns `status: "success"` with `action_result` (including the transaction ID), or `status: "rejected"`. Deciding a workflow that is not awaiting approval, or that already has a recorded decision, returns `409`; an unknown `thread_id` returns `404`.

## Audit trail

Every workflow produces a chronological, append-only record of what happened, who did it and what it referred to. It is stored in the `audit_events` table, separate from LangGraph's checkpoints (which hold internal graph state and are not an audit record).

### What is recorded

| Event type | Recorded by | When |
|---|---|---|
| `workflow_started` | `create_workflow_run` (same transaction) | A workflow is created via the API |
| `node_started` / `node_completed` / `node_failed` | Node observer (`workflow_audit.py`) | Each business node (`employee`, `policy`, `rag`, `approval`, `action`, `rejection`, `final`, `recovery`) starts, finishes, reports a failure or raises |
| `approval_requested` | API | The workflow pauses for human approval |
| `approval_approved` / `approval_rejected` | `record_approval_decision` (same transaction) | A decision is stored (`actor_type: human`) |
| `workflow_resumed` | API | The workflow resumes after a decision |
| `business_action_executed` | `record_expense_reimbursement` (same transaction) | A reimbursement is recorded (once per workflow) |
| `business_action_failed` | Business action service | Recording the reimbursement failed |
| `workflow_completed` / `workflow_rejected` / `workflow_failed` | API | The workflow ends |
| `workflow_recovery` | API | A lookup failed and the recovery path ended the workflow |
| `workflow_error` | API | Execution raised an unexpected error |

Events written "in the same transaction" commit or roll back together with the business record. All other events are best-effort: if writing them fails, the error is logged and the workflow continues.

Example (approved $750 expense, node events omitted): `workflow_started` → `approval_requested` → `approval_approved` → `workflow_resumed` → `business_action_executed` → `workflow_completed`.

### Event fields

`id`, `event_id` (UUID), `occurred_at`, `thread_id`, `event_type`, `actor_type` (`system` / `human` / `agent`), `actor_id`, `node`, `entity_type` + `entity_id` (e.g. `reimbursement` / `EXP-E102-…`), `status` (`success` / `failure` / `pending` / `info`), `message`, `details` (JSON), `error_type`, `error_message`.

Events reference business records instead of copying them. The question, approval comments, document text, LLM answers and employee names are never stored in audit events, and `details` values under keys that look like secrets (password, token, api_key, …) are redacted.

### Append-only protection

Migration `0003` adds database triggers that reject `UPDATE` and `DELETE` on `audit_events` (and `TRUNCATE` on PostgreSQL); `INSERT` works normally. The same rule is enforced on SQLite, so the offline tests cover it. A rejected change raises an integrity error:

```
audit_events is append-only: UPDATE is not allowed
```

This protects the audit history from application bugs and accidental changes. A database owner or superuser can still remove the triggers, so it is not protection against a malicious administrator.

### Audit API

**Workflow history**

```http
GET /workflows/{thread_id}/audit?limit=100&after_id=<id>
```

Returns the workflow's events oldest first: `{"thread_id", "events", "count", "limit", "next_after_id"}`. Returns `404` if the workflow has neither a run record nor any audit events.

**Search across workflows**

```http
GET /audit-events?thread_id=&event_type=&entity_type=&entity_id=&limit=100&after_id=<id>
```

All filters are optional and combined with AND; `event_type` must be one of the event types above. Returns `{"events", "count", "limit", "next_after_id"}`; no match returns an empty page. For example, `GET /audit-events?entity_type=reimbursement&entity_id=EXP-E102-7F4DE628` finds the events for one reimbursement.

**Pagination (both endpoints):** `limit` is 1–500 (default 100). Pass `next_after_id` from a response as `after_id` to get the next page; it is `null` when the page was not full.

## Enterprise connectors

Connectors read an external system and map it into the [context graph](#audit-trail) (PostgreSQL). They run on demand from the command line; there is deliberately no HTTP endpoint for them, because the API has no authentication yet.

```bash
python -m app.services.connectors list          # which connectors are configured
python -m app.services.connectors sync entra    # run one and apply it to the graph
```

The first connector is **Microsoft Entra ID** (via Microsoft Graph). Set `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` and `ENTRA_CLIENT_SECRET` in `.env`; the app registration needs the `User.Read.All` application permission with admin consent. It authenticates with the OAuth 2.0 client-credentials flow and reads only ids, display names, job titles, departments and employee ids (no email addresses or phone numbers).

| Directory data | Context graph |
|---|---|
| user | `person:entra/<object id>` node (inactive if the account is disabled) |
| manager | `reports_to` edge between the two people |
| people who manage others | `has_role` edge to `role:manager` |
| department | `member_of` edge to `department:<name>` (the existing node if there is one) |
| `employeeId` matching an employee | `employee:<id>` `identified_as` the person node |

**Safety rules** (shared by every connector, see `connectors/base.py` and `connectors/sync.py`):

- A connector owns only what it created (`source_type` `connector`, `source_ref` starting `<name>:`). Nodes owned by anything else, such as departments from the employees table, are never changed, only linked to.
- Data that disappears from the external system is deactivated (nodes) or removed (edges), but only after a *complete* fetch. A failed or partial fetch changes nothing, and an empty result is refused while the connector still owns data (override with `--allow-empty`).
- A sync is one transaction: the graph changes and the `connector_sync_completed` audit event commit together. Start, completion and failure are in the audit trail (`GET /audit-events?event_type=connector_sync_completed`), with counts and no secrets.
- Transient failures (network errors, timeouts, HTTP 408/429/5xx) are retried with exponential back-off and `Retry-After`; authentication and permission errors are not. Redirects are not followed, pagination links to another host are refused, and error messages never include credentials, tokens, URLs or response bodies.

To add a connector, subclass `Connector` (`name`, `collect() -> ConnectorBatch`), register it in `connectors/__init__.py`, and reuse `ConnectorHttpClient` for retries and `ClientCredentialsAuth` for OAuth.

## Dashboard

A React dashboard in `frontend/` shows the workflows, the approvals queue, workflow discovery, automation recommendations, the audit trail and the context graph. It is a separate app that calls the API above.

```bash
# terminal 1: the API
cd backend
uvicorn app.main:app --reload

# terminal 2: the dashboard (first time: npm install)
cd frontend
npm run dev          # open http://localhost:5173
```

In development Vite forwards `/api/*` to the backend on port 8000, so no CORS setup is needed. See [frontend/README.md](frontend/README.md) for the pages, configuration, tests and notes. Like the API, the dashboard has **no authentication**.

## Workflow discovery

`GET /discovery/workflows` describes how the expense process **actually runs**, learned from what is already recorded (no new tables, nothing stored; it is computed on each request):

```bash
curl "http://127.0.0.1:8000/discovery/workflows?limit=500&since=2026-10-01T00:00:00Z"
```

| Section | What it shows |
|---|---|
| `window` | How many runs were analysed and their time span |
| `outcomes` | `completed`, `rejected`, `failed`, `error`, `awaiting_approval`, `in_progress` |
| `variants` | The distinct paths runs took (parallel nodes share a stage), how often, their outcomes and cycle time, and whether a person had to approve (`human_approval`) |
| `steps` | Per node: runs, failures, failure rate and duration (mean, median, p95, max), plus the modeled process steps it implements |
| `approvals` | Runs needing approval, approved / rejected / pending, rejection rate and the wait from request to decision |
| `volume` | Runs by department and by amount band (the bands come from the approval thresholds in the context graph's policy rules) |
| `process_model` | The modeled process from the context graph set against the automation: each step is `automated`, `partial` or `manual`, `automation_gaps` lists the manual and partial ones with who performs them, the systems and the document evidence, and `unmapped_nodes` are nodes that ran but implement no modeled step |

Parameters: `limit` (most recent N runs, 1-5000, default 1000), `since` / `until` (ISO 8601, filter by start time). The sources are the audit trail (`audit_events`), `workflow_runs` / `employees` and the context graph; only runs with a `workflow_started` event (made through the API) are analysed. Discovery reports facts only: deciding what to automate is the next roadmap step.

## Automation recommendations

`GET /automation/recommendations` turns what [workflow discovery](#workflow-discovery) observed into a ranked list of what to automate or improve next. It is deterministic and read-only (no LLM, nothing stored, nothing executed); it takes the same `limit`, `since` and `until` parameters as discovery, plus `min_priority` (`low`, `medium` or `high`).

| Type | Raised when | Example |
|---|---|---|
| `automate_manual_step` | a modeled process step has no node implementing it | "Automate: Notify employee of the final decision" |
| `complete_partial_automation` | a step is only partly implemented | the finance approval for amounts above $5,000, which today shares one approval decision |
| `reduce_step_failures` | a node fails in at least 5% of at least 5 executions | employee lookup failing on missing or unknown IDs |
| `reduce_approval_wait` | waiting for approvers is at least half the cycle time of claims that need approval | reminders and escalation; delegated approval is only floated with 20+ decisions and a rejection rate of 10% or less, and is marked `requires_human_decision` |

Every recommendation carries a `score` from 0 to 100, a `priority`, a `confidence` level, an `effort` estimate, the observed facts behind it (`rationale`, `metrics`), the process-model `evidence` (document lines), and `suggested_actions`.

```
score = 100 * (0.5 * impact + 0.3 * feasibility + 0.2 * confidence)
```

- **impact**: observed volume (log scale, 1.0 at 1,000 runs), and per type the share of claims affected, the failure rate or the approval wait.
- **feasibility**: a fixed heuristic from what the step does (notification, system integration, rule-based validation, intake) and whether the process model names a system to integrate with.
- **confidence**: how many runs were analysed (`high` from 50, `medium` from 10).
- Priority is `high` from 70 and `medium` from 45.

The weights and thresholds are constants in `automation_recommendation_service.py` and the rubric is returned in the response (`method`), so a ranking can always be explained. Recommendations are advice for people: nothing is changed or executed.

## Current limitations

- **Workflow state is durable only on PostgreSQL**: with the SQLite default, `MemorySaver` loses pending approvals on restart.
- **Checkpoints are never cleaned up**: finished workflows keep their checkpoint rows in PostgreSQL.
- **Single approval step**: expenses over $5,000 require manager *and* finance approval, but only one decision is collected.
- **Approver is not authenticated**: `approver` is free text supplied by the caller; there is no user authentication yet.
- **Reimbursement status** is always `approved`, including expenses that needed no approval (`workflow_runs.approval_status` shows `not_required`).
- **Audit API is unauthenticated**, like all endpoints: anyone who can reach the API can read the audit history.
- **Audit events for direct graph runs**: scripts that run the graph without the API record only the events written inside services (`business_action_executed` / `business_action_failed`).
- **Audit retention**: audit events are never pruned; the append-only triggers must be removed by an administrator for any archival.
- **Audit search filters**: `/audit-events` filters by thread, event type and entity; status and time-range filters are not available yet.
- **Regex-based extraction**: the graph takes the employee ID and the *first* positive number in the question as the amount.
- **RAG context formatting**: `rag_node` passes the raw search-result dict as context text.
- **Structured output** is parsed with `json.loads` without schema validation and is not yet used by the graph.
- **Only `expense_process.txt` is ingested**; the travel-expense policy PDF in `data/` has not been loaded.
- **Import-time setup**: the Cohere clients and ChromaDB open when modules are imported.
- **Workflow runs recorded via the API only**: graphs run directly from scripts only get a run row when they reach the business action.
- `annual_budget` is not yet used for budget checks.
- **Migrations run at app startup**: fine for a single process; with multiple workers, run `alembic upgrade head` once before starting them.
- `OPENAI_API_KEY` is not used by the current code.
- **Dashboard: paused workflows and SQLite**: with the default SQLite database the workflow engine keeps paused state in memory, so after a backend restart the approvals queue (read from the database) can list a run that can no longer be resumed. Use PostgreSQL for durable approvals.
- **Dashboard has no authentication**: nothing in the stack does. The Docker setup serves it on an unauthenticated port, so put it behind something that authenticates before exposing it beyond your machine.
- **First boot is slow**: the backend runs every Alembic migration and creates the checkpoint tables on an empty database, which took about 70 seconds, so the container healthcheck allows a 180-second start period. The dashboard container waits for it.
- **One backend container**: it runs Alembic at startup and keeps metrics in process, so `docker compose up --scale backend=2` would run migrations twice and split the metrics. Scaling out needs a separate migration step first.
- **Workflow discovery is computed on demand** from the most recent runs (up to 5,000 per request); results are not stored, so there is no history of earlier discoveries.
- **Discovery only sees automated steps**: manual steps (receipt validation, budget check, notification, ...) happen outside the system, so they appear as gaps in the process model but with no observed runs or timings. Runs started directly from scripts (without the API) are not analysed.
- **Discovery reads the audit trail's node events**: the `context` node is not audited (by design), so it does not appear in paths or step statistics.
- **Recommendations are heuristics, not measurements**: manual steps happen outside the system, so their impact uses the claim volume (not measured effort or cost), and feasibility comes from a fixed catalogue keyed on words in the step label. They are advice only; accepting or dismissing a recommendation is not recorded anywhere yet.
- **Recommendations are computed on demand** from discovery, so they change as runs accumulate; with fewer than 10 runs every confidence is low.
- **Connector syncs are manual**: nothing schedules them, and two syncs run at the same time are not coordinated (each is atomic and idempotent).
- **Entra connector scope**: users only (no groups), a full read on every sync (no delta queries), and directory managers are not matched to the free-text managers in the employees table.
- **Metrics are per process and in memory**: they reset on restart, and with several workers a `/metrics` scrape returns one worker's view. A shared store (a Prometheus client library, or a push gateway) is the upgrade.
- **No distributed tracing**: a span is a flat measurement, not a tree, and the recent-activity view holds the last 200 operations in memory. The call sites are the ones OpenTelemetry would wrap, so that swap is contained.
- **`/metrics` and `/observability/summary` are unauthenticated**, like every other endpoint: they expose route names, timings and recent error messages. Do not expose them publicly.
- **Readiness does not prove Cohere works**, only that a key is configured; a revoked key still reports ready.
- **Evals cover the retrieval and generation steps, not the whole workflow**: there is no end-to-end eval that runs the graph, because that would write business actions to the database.
- **Model evals need a recording or live credits**: no cassette is committed, so `retrieval` and `grounding` report every case as skipped until someone runs `--live --record`. The `retrieval` scores then describe whatever documents are in that Chroma store.
- **Eval thresholds are fixed in code** (`app/evals/suites/`), and results are printed or written to a file; nothing stores a history, so a trend over time has to be kept outside the project.
- **Connector data does not yet influence answers**: the RAG context and `get_business_context` read the existing graph relationships only; the new `identified_as` / directory edges are visible through the context API.

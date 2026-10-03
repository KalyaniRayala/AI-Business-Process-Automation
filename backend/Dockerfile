# Backend image: FastAPI + LangGraph + ChromaDB.
#
# Two stages so the compiler toolchain needed to build any wheel that
# has no binary release stays out of the final image.

# ============================================================
# BUILD
# ============================================================

FROM python:3.13-slim AS build

# Compilers for the few packages that may need to build from source
# on this platform; nothing from this stage reaches the runtime image.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Its own virtualenv, so the runtime stage copies one directory.
RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Dependencies first: this layer is rebuilt only when the pinned
# requirements change, not on every code edit.
COPY requirements.txt ./

RUN pip install --requirement requirements.txt


# ============================================================
# RUNTIME
# ============================================================

FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Written to by the application, so it lives outside /app on a
    # volume that survives a rebuild. Not to be confused with
    # /app/data, which holds the sample documents from the repo.
    CHROMA_PATH=/var/lib/bpa/chroma_db \
    LOG_FORMAT=json

COPY --from=build /opt/venv /opt/venv

# An unprivileged account: nothing here needs root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /var/lib/bpa/chroma_db \
    && chown -R appuser:appuser /var/lib/bpa

WORKDIR /app

# .dockerignore keeps .env, the SQLite database, the local Chroma
# store and the caches out of the image.
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# Readiness, not liveness: the container is healthy once the
# database and the vector store answer, which is what makes
# depends_on: service_healthy meaningful for the frontend.
#
# The timings are set by what the first boot actually does: Alembic
# runs every migration and the checkpointer creates its tables,
# which took about 70s on an empty database, so the start period is
# generous and start-interval probes often inside it. The timeout
# allows for the probe opening the database and the vector store on
# a cold cache; the warm call is a couple of seconds.
HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --start-interval=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=12).status == 200 else 1)"]

# One worker: the app runs Alembic at startup and keeps its metrics
# in process. Scaling out means running migrations separately and
# scraping each worker (see the README).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

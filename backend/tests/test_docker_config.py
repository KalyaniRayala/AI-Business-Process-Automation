"""
The container configuration.

Docker files are code that nothing else type-checks, and the
mistakes they invite are expensive: a password baked into an image,
a database published to the internet, a container running as root,
a proxy that does not match what the dashboard calls. These tests
read the files and assert the properties that must hold. They do
not need Docker installed.
"""

import re
from pathlib import Path

import pytest
import yaml


# backend/tests/test_docker_config.py -> the project root
ROOT = Path(__file__).resolve().parents[2]

COMPOSE_FILE = ROOT / "docker-compose.yml"
BACKEND_DOCKERFILE = ROOT / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE = ROOT / "frontend" / "Dockerfile"
NGINX_CONF = ROOT / "frontend" / "nginx.conf"
BACKEND_IGNORE = ROOT / "backend" / ".dockerignore"
FRONTEND_IGNORE = ROOT / "frontend" / ".dockerignore"
ENV_EXAMPLE = ROOT / ".env.docker.example"


def read(path: Path) -> str:

    assert path.is_file(), f"{path} is missing"

    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> dict:

    return yaml.safe_load(read(COMPOSE_FILE))


@pytest.fixture(scope="module")
def services(compose) -> dict:

    return compose["services"]


# ============================================================
# FILES
# ============================================================

@pytest.mark.parametrize(
    "path",
    [
        COMPOSE_FILE,
        BACKEND_DOCKERFILE,
        FRONTEND_DOCKERFILE,
        NGINX_CONF,
        BACKEND_IGNORE,
        FRONTEND_IGNORE,
        ENV_EXAMPLE,
    ],
    ids=lambda path: path.name,
)
def test_the_file_exists(path):

    assert path.is_file()


def test_the_compose_file_is_valid_yaml(compose):

    assert isinstance(compose, dict)
    assert "services" in compose


def test_every_service_is_defined(services):

    assert set(services) == {"db", "backend", "frontend"}


# ============================================================
# SECRETS
# ============================================================

def test_no_secret_is_written_into_the_compose_file(compose):
    """
    Every secret must come from the environment. A literal value
    here would be committed to the repository.
    """

    text = read(COMPOSE_FILE)

    for line in text.splitlines():

        stripped = line.strip()

        if stripped.startswith("#"):
            continue

        for name in ("POSTGRES_PASSWORD", "COHERE_API_KEY"):

            if name in stripped and ":" in stripped:
                assert "${" in stripped, f"{name} must come from the environment: {line}"


def test_the_required_secrets_fail_loudly_when_unset(compose):
    """`${VAR:?message}` beats a default password shared by everyone."""

    text = read(COMPOSE_FILE)

    assert "${POSTGRES_PASSWORD:?" in text
    assert "${COHERE_API_KEY:?" in text


def test_every_compose_variable_is_documented():
    """
    A variable the compose file reads but nobody writes down is a
    setting only its author knows about.
    """

    code = "\n".join(
        line
        for line in read(COMPOSE_FILE).splitlines()
        if not line.strip().startswith("#")
    )

    referenced = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", code))

    documented = {
        line.lstrip("# ").split("=", 1)[0].strip()
        for line in read(ENV_EXAMPLE).splitlines()
        if "=" in line
    }

    assert referenced <= documented, sorted(referenced - documented)


def test_the_example_env_file_holds_no_real_values():

    text = read(ENV_EXAMPLE)

    assert "your-cohere-api-key" in text
    assert "choose-a-password" in text

    # A real Cohere key is a long opaque token; a placeholder is not.
    for line in text.splitlines():

        if line.startswith("COHERE_API_KEY="):
            assert len(line.split("=", 1)[1]) < 40


def test_the_env_file_is_ignored_by_git():

    assert ".env" in read(ROOT / ".gitignore").splitlines()


@pytest.mark.parametrize("path", [BACKEND_IGNORE, FRONTEND_IGNORE], ids=["backend", "frontend"])
def test_the_build_context_excludes_env_files(path):

    assert ".env" in read(path).splitlines()


def test_the_backend_image_excludes_local_state():

    text = read(BACKEND_IGNORE)

    assert "chroma_db/" in text
    assert "data/business.db" in text


def test_the_frontend_image_excludes_node_modules():

    assert "node_modules/" in read(FRONTEND_IGNORE)


# ============================================================
# DATABASE SERVICE
# ============================================================

def test_the_database_is_not_published_by_default(services):
    """An exposed database port is how a test environment leaks."""

    assert "ports" not in services["db"]


def test_the_database_keeps_its_data_on_a_volume(services, compose):

    volumes = services["db"]["volumes"]

    # postgres:18+ keeps its data in a major-version subdirectory and
    # refuses to start when the volume is mounted at the old
    # /var/lib/postgresql/data path, so the parent is the right target.
    assert any(volume.endswith(":/var/lib/postgresql") for volume in volumes)

    assert not any(
        volume.endswith(":/var/lib/postgresql/data") for volume in volumes
    ), "postgres 18+ rejects a mount at the data subdirectory"

    assert "pgdata" in compose["volumes"]


def test_the_database_has_a_health_check(services):

    assert "pg_isready" in str(services["db"]["healthcheck"]["test"])


# ============================================================
# BACKEND SERVICE
# ============================================================

def test_the_backend_builds_from_its_own_directory(services):

    assert services["backend"]["build"]["context"] == "./backend"


def test_the_backend_waits_for_a_healthy_database(services):

    assert services["backend"]["depends_on"]["db"]["condition"] == "service_healthy"


def test_the_backend_points_at_the_database_service(services):

    url = services["backend"]["environment"]["DATABASE_URL"]

    assert url.startswith("postgresql+psycopg://")
    assert "@db:5432/" in url


def test_the_backend_is_published_on_the_loopback_interface_only(services):

    for mapping in services["backend"]["ports"]:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_the_vector_store_is_on_a_volume(services, compose):

    volumes = services["backend"]["volumes"]

    assert any("chroma" in volume for volume in volumes)
    assert "chroma" in compose["volumes"]


def test_the_backend_volume_matches_the_path_the_image_uses():
    """A mismatch would silently give an empty vector store."""

    dockerfile = read(BACKEND_DOCKERFILE)

    match = re.search(r"CHROMA_PATH=(\S+)", dockerfile)

    assert match, "the image must set CHROMA_PATH"

    path = match.group(1)

    assert path in read(COMPOSE_FILE)


# ============================================================
# FRONTEND SERVICE
# ============================================================

def test_the_frontend_waits_for_a_healthy_backend(services):

    assert services["frontend"]["depends_on"]["backend"]["condition"] == "service_healthy"


def test_the_frontend_is_published(services):

    assert services["frontend"]["ports"]


def test_the_api_base_matches_the_proxy(services):
    """
    The page calls /api, so nginx has to answer on /api. These two
    are the easiest pair in the stack to let drift apart.
    """

    assert services["frontend"]["build"]["args"]["VITE_API_BASE_URL"] == "/api"

    assert "location /api/" in read(NGINX_CONF)


# ============================================================
# BACKEND IMAGE
# ============================================================

def test_the_backend_image_runs_as_a_normal_user():

    text = read(BACKEND_DOCKERFILE)

    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)

    assert users, "the image must switch away from root"
    assert users[-1] != "root"


def test_the_backend_image_is_multi_stage():
    """The compiler toolchain must not ship in the runtime image."""

    text = read(BACKEND_DOCKERFILE)

    assert len(re.findall(r"^FROM ", text, re.MULTILINE)) >= 2
    assert "build-essential" in text
    assert "COPY --from=build" in text


def test_the_backend_image_installs_the_pinned_requirements():

    assert "pip install --requirement requirements.txt" in read(BACKEND_DOCKERFILE)


def test_the_backend_image_has_a_health_check():

    text = read(BACKEND_DOCKERFILE)

    assert "HEALTHCHECK" in text
    assert "/health/ready" in text


def test_the_backend_image_serves_on_all_interfaces():
    """Inside a container, binding to localhost would be unreachable."""

    assert "--host" in read(BACKEND_DOCKERFILE)
    assert "0.0.0.0" in read(BACKEND_DOCKERFILE)


# ============================================================
# FRONTEND IMAGE
# ============================================================

def test_the_frontend_image_builds_then_serves_static_files():

    text = read(FRONTEND_DOCKERFILE)

    assert "node:22-slim AS build" in text
    assert "nginx:alpine" in text
    assert "COPY --from=build /app/dist" in text


def test_the_frontend_image_installs_from_the_lockfile():
    """npm ci, not npm install: the lockfile is the pinned version."""

    text = read(FRONTEND_DOCKERFILE)

    assert "npm ci" in text
    assert ".npmrc" in text


def test_the_frontend_build_type_checks():

    assert "npm run build" in read(FRONTEND_DOCKERFILE)


def test_the_node_version_satisfies_the_package_requirement():

    import json

    package = json.loads(read(ROOT / "frontend" / "package.json"))

    required = package["engines"]["node"]

    assert required == ">=22.12"

    # The image tag has to satisfy it.
    assert "node:22" in read(FRONTEND_DOCKERFILE)


# ============================================================
# NGINX
# ============================================================

def test_nginx_forwards_the_api_to_the_backend_service():

    text = read(NGINX_CONF)

    assert "server backend:8000;" in text

    # The trailing slash is what strips the /api prefix; without it
    # the backend would receive /api/workflows and answer 404.
    assert re.search(r"proxy_pass\s+http://backend/;", text)


def test_nginx_falls_back_to_the_app_for_client_routes():
    """Reloading /approvals must serve the app, not a 404."""

    assert "try_files $uri $uri/ /index.html;" in read(NGINX_CONF)


def test_nginx_does_not_cache_the_entry_point():

    text = read(NGINX_CONF)

    assert "location = /index.html" in text
    assert 'Cache-Control "no-cache"' in text


def test_nginx_passes_the_correlation_id():

    text = read(NGINX_CONF)

    assert "X-Request-ID" in text


def test_the_nginx_blocks_are_balanced():
    """
    nginx is not parsed anywhere else in this repository, so an
    unclosed block would only show up as a container that will not
    start.
    """

    text = read(NGINX_CONF)

    depth = 0

    for line in text.splitlines():

        code = line.split("#", 1)[0]

        depth += code.count("{") - code.count("}")

        assert depth >= 0, f"a block closes before it opens: {line}"

    assert depth == 0, "a block is left open"


def test_every_nginx_directive_is_terminated():

    for line in read(NGINX_CONF).splitlines():

        code = line.split("#", 1)[0].strip()

        if not code or code in ("}",) or code.endswith(("{", "}")):
            continue

        assert code.endswith(";"), f"missing semicolon: {line}"


def test_nginx_allows_a_slow_workflow_to_finish():
    """A run waits on the model, past nginx's one-minute default."""

    match = re.search(r"proxy_read_timeout\s+(\d+)s", read(NGINX_CONF))

    assert match
    assert int(match.group(1)) >= 120

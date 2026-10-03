"""
Microsoft Entra ID (Azure AD) directory connector, via Microsoft Graph.

Authenticates as an application (OAuth 2.0 client credentials; the
app registration needs the User.Read.All application permission with
admin consent), reads the user directory and maps it to the context
graph:

    person:entra/<object id>        one per directory user
        --reports_to-->  person:entra/<manager's object id>
        --member_of-->   department:<slug>    (linked to the existing
                                               department node if any)
        --has_role-->    role:manager         (users who manage others)
    employee:<employeeId> --identified_as--> person:entra/<object id>
        (only when the directory's employeeId matches an employee
        node, which connects the HR record to its directory identity)

Disabled accounts become inactive nodes. Only identifiers, display
names, job titles, departments and employee ids are read (no email
addresses, phone numbers or other personal attributes).
"""

import logging
import random
import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from app.services.connectors.base import (
    Connector,
    ConnectorAuthError,
    ConnectorBatch,
    ConnectorDataError,
    ConnectorRequestError,
    EdgeSpec,
    NodeSpec,
)
from app.services.connectors.http import (
    ClientCredentialsAuth,
    ConnectorHttpClient,
)
from app.services.connectors.settings import EntraSettings, HttpSettings
from app.services.context_graph_seed import MANAGER_ROLE, department_key, slug


logger = logging.getLogger(__name__)


GRAPH_SCOPE = "https://graph.microsoft.com/.default"

USER_FIELDS = "id,displayName,department,jobTitle,employeeId,accountEnabled"

PAGE_SIZE = 100

# Object ids are GUIDs; keys and references stay URL/key safe.
USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,63}$")

# Same shape as employee ids in the employees table (e.g. E102).
EMPLOYEE_ID = re.compile(r"^[A-Za-z0-9_-]{1,20}$")

MAX_TEXT_LENGTH = 200


def person_key(user_id: str) -> str:

    return f"person:entra/{user_id}"


def clean_text(value) -> str | None:

    if not isinstance(value, str):
        return None

    value = " ".join(value.split())

    return value[:MAX_TEXT_LENGTH] or None


# ============================================================
# MAPPING (pure: directory users in, graph batch out)
# ============================================================

def build_batch(users: list) -> ConnectorBatch:

    batch = ConnectorBatch()

    valid: dict[str, dict] = {}

    for user in users:

        user_id = user.get("id") if isinstance(user, dict) else None

        if (
            not isinstance(user_id, str)
            or not USER_ID.match(user_id)
            or user_id in valid
        ):

            batch.skipped_records += 1
            continue

        valid[user_id] = user

    departments: dict[str, NodeSpec] = {}
    edges: dict[tuple[str, str, str], EdgeSpec] = {}
    manager_ids: set[str] = set()

    def add_edge(from_key, edge_type, to_key, source_ref):

        edges.setdefault(
            (from_key, edge_type, to_key),
            EdgeSpec(from_key, edge_type, to_key, source_ref)
        )

    for user_id, user in valid.items():

        key = person_key(user_id)
        source_ref = f"entra:users/{user_id}"

        department = clean_text(user.get("department"))

        properties = {
            "job_title": clean_text(user.get("jobTitle")),
            "department": department,
            "employee_id": clean_text(user.get("employeeId")),
            "account_enabled": user.get("accountEnabled") is not False,
        }

        batch.nodes.append(NodeSpec(
            node_key=key,
            node_type="person",
            label=clean_text(user.get("displayName")) or user_id,
            source_ref=source_ref,
            properties={k: v for k, v in properties.items() if v is not None},
            is_active=user.get("accountEnabled") is not False,
        ))

        # Reporting line (only to managers present in the directory).
        manager = user.get("manager")
        manager_id = manager.get("id") if isinstance(manager, dict) else None

        if manager_id in valid and manager_id != user_id:

            add_edge(key, "reports_to", person_key(manager_id), source_ref)
            manager_ids.add(manager_id)

        # Department.
        if department and slug(department):

            dept_key = department_key(department)

            departments.setdefault(dept_key, NodeSpec(
                node_key=dept_key,
                node_type="department",
                label=department,
                source_ref=f"entra:departments/{slug(department)}",
            ))

            add_edge(key, "member_of", dept_key, source_ref)

        # HR record <-> directory identity.
        employee_id = clean_text(user.get("employeeId"))

        if employee_id and EMPLOYEE_ID.match(employee_id.upper()):

            add_edge(
                f"employee:{employee_id.upper()}", "identified_as", key,
                source_ref
            )

    # People who manage others hold the manager role, as the
    # employee projection already models for managers.
    for manager_id in sorted(manager_ids):

        add_edge(
            person_key(manager_id), "has_role", MANAGER_ROLE,
            f"entra:users/{manager_id}/manager"
        )

    batch.nodes.extend(departments.values())
    batch.edges.extend(edges.values())

    return batch


# ============================================================
# CONNECTOR
# ============================================================

class EntraConnector(Connector):

    name = "entra"

    def __init__(
        self,
        settings: EntraSettings,
        http_settings: HttpSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):

        self.settings = settings
        self.http_settings = http_settings or HttpSettings()

        self._transport = transport
        self._sleep = sleep
        self._jitter = jitter

    def collect(self) -> ConnectorBatch:

        with ConnectorHttpClient(
            self.http_settings,
            transport=self._transport,
            sleep=self._sleep,
            jitter=self._jitter,
        ) as http:

            auth = ClientCredentialsAuth(
                http,
                token_url=(
                    f"{self.settings.authority_url}/"
                    f"{self.settings.tenant_id}/oauth2/v2.0/token"
                ),
                client_id=self.settings.client_id,
                client_secret=self.settings.client_secret,
                scope=GRAPH_SCOPE,
            )

            users = self.fetch_users(http, auth)

        logger.info("Entra connector read %d directory users", len(users))

        return build_batch(users)

    # --------------------------------------------------------
    # Microsoft Graph requests
    # --------------------------------------------------------

    def fetch_users(
        self, http: ConnectorHttpClient, auth: ClientCredentialsAuth
    ) -> list:
        """
        All users, following @odata.nextLink. Any failure raises:
        a partial directory would deactivate people who are merely
        missing from the pages read so far.
        """

        base = urlparse(self.settings.graph_url)

        url = f"{self.settings.graph_url}/users"
        params = {
            "$select": USER_FIELDS,
            "$expand": "manager($select=id)",
            "$top": str(PAGE_SIZE),
        }

        users: list = []
        pages = 0

        while url:

            pages += 1

            if pages > self.http_settings.max_pages:

                raise ConnectorDataError(
                    "Directory has more than "
                    f"{self.http_settings.max_pages} pages; "
                    "raise CONNECTOR_MAX_PAGES if this is expected."
                )

            body = self.get_json(http, auth, url, params)

            values = body.get("value") if isinstance(body, dict) else None

            if not isinstance(values, list):

                raise ConnectorDataError(
                    "Microsoft Graph response has no 'value' list."
                )

            users.extend(values)

            url = body.get("@odata.nextLink")
            params = None

            if url is not None:

                # The bearer token must only go to the configured host.
                link = urlparse(url) if isinstance(url, str) else None

                if (
                    link is None
                    or link.scheme != base.scheme
                    or link.netloc != base.netloc
                ):

                    raise ConnectorDataError(
                        "Microsoft Graph returned a next page link for "
                        "an unexpected host."
                    )

        return users

    def get_json(self, http, auth, url, params) -> dict:

        for refreshed in (False, True):

            response = http.send(
                "GET", url, params=params,
                headers={
                    "Authorization": f"Bearer {auth.token()}",
                    "Accept": "application/json",
                },
            )

            # An expired or revoked token: get a new one once.
            if response.status_code == 401 and not refreshed:

                auth.invalidate()
                continue

            break

        if response.status_code == 200:

            try:
                return response.json()
            except ValueError:
                raise ConnectorDataError(
                    "Microsoft Graph returned invalid JSON."
                ) from None

        code = self.graph_error_code(response)

        if response.status_code == 401:

            raise ConnectorAuthError(
                "Microsoft Graph rejected the access token."
            )

        if response.status_code == 403:

            raise ConnectorAuthError(
                "Microsoft Graph denied access; the app registration "
                "needs the User.Read.All application permission with "
                f"admin consent{code}."
            )

        raise ConnectorRequestError(
            f"Microsoft Graph request failed (HTTP {response.status_code}"
            f"{code})."
        )

    @staticmethod
    def graph_error_code(response: httpx.Response) -> str:
        """Graph's short error code (e.g. Authorization_RequestDenied)."""

        try:
            code = response.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
            return ""

        if isinstance(code, str) and code.replace("_", "").isalnum():
            return f": {code[:60]}"

        return ""

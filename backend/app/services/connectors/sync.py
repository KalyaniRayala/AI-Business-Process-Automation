"""
Apply a connector's batch to the context graph, with audit events.

The external fetch happens first, outside any database transaction.
The graph changes and the "completed" audit event then commit
together in one transaction, so a sync either applies fully or not
at all. Failures are recorded as "failed" events (best-effort).

Ownership rules (see connectors.base):
- Nodes the connector owns (source_type 'connector', source_ref
  '<name>:...') are created, updated and, when the system stops
  returning them, deactivated.
- A node that already exists under another owner (for example the
  department node projected from the employees table) is left
  untouched; edges may still link to it.
- Edges the connector owns that are no longer returned are removed.
- An edge whose endpoint does not exist is skipped and counted.
- A connector that returns nothing while it still owns active nodes
  is refused, unless allow_empty is set: an empty answer is more
  likely a permission or filter problem than an empty company.
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.services.audit_service import (
    AuditEventType,
    add_event,
    record_event,
)
from app.services.connectors.base import (
    Connector,
    ConnectorBatch,
    ConnectorDataError,
    ConnectorError,
)
from app.services.context_graph_service import (
    SourceType,
    upsert_edge,
    upsert_node,
)
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode


logger = logging.getLogger(__name__)


CONNECTOR_NAME = re.compile(r"^[a-z][a-z0-9_]{0,29}$")

# Keeps IN (...) lists below SQLite's variable limit.
LOOKUP_CHUNK = 500


# ============================================================
# ENTRY POINT
# ============================================================

def sync_connector(connector: Connector, *, allow_empty: bool = False) -> dict:
    """
    Run one connector and apply its batch. Returns the summary
    counts (also stored in the audit trail). Raises on failure.
    """

    name = connector.name

    if not CONNECTOR_NAME.match(name):
        raise ValueError(f"Invalid connector name: {name!r}")

    audit = {"entity_type": "connector", "entity_id": name}

    record_event(
        event_type=AuditEventType.CONNECTOR_SYNC_STARTED,
        message=f"Connector {name} sync started.",
        status="info",
        **audit
    )

    try:

        batch = connector.collect()

        check_batch(batch, name)

        with session_scope() as session:

            summary = apply_batch(session, batch, name, allow_empty)

            add_event(
                session,
                event_type=AuditEventType.CONNECTOR_SYNC_COMPLETED,
                message=f"Connector {name} sync completed.",
                status="success",
                details=summary,
                **audit
            )

    except Exception as error:

        if isinstance(error, ConnectorError):
            message = str(error)
        else:
            # Unexpected errors (e.g. database) may carry SQL text.
            logger.exception("Connector %s sync failed", name)
            message = "Unexpected error; see the application log."

        record_event(
            event_type=AuditEventType.CONNECTOR_SYNC_FAILED,
            message=f"Connector {name} sync failed.",
            status="failure",
            error_type=type(error).__name__,
            error_message=message,
            **audit
        )

        raise

    logger.info("Connector %s sync completed: %s", name, summary)

    return summary


# ============================================================
# VALIDATION
# ============================================================

def check_batch(batch: ConnectorBatch, name: str) -> None:
    """A connector may only describe its own data."""

    prefix = f"{name}:"

    keys = set()

    for node in batch.nodes:

        if not node.source_ref.startswith(prefix):

            raise ConnectorDataError(
                f"Node {node.node_key!r} has a source_ref outside "
                f"the {name!r} connector."
            )

        if node.node_key in keys:

            raise ConnectorDataError(f"Duplicate node {node.node_key!r}.")

        keys.add(node.node_key)

    for edge in batch.edges:

        if not edge.source_ref.startswith(prefix):

            raise ConnectorDataError(
                f"Edge {edge.from_key!r} -> {edge.to_key!r} has a "
                f"source_ref outside the {name!r} connector."
            )


# ============================================================
# APPLY
# ============================================================

def apply_batch(
    session, batch: ConnectorBatch, name: str, allow_empty: bool = False
) -> dict:

    prefix = f"{name}:"

    summary = {
        "connector": name,
        "nodes_created": 0, "nodes_updated": 0, "nodes_unchanged": 0,
        "nodes_deactivated": 0, "nodes_left_unchanged": 0,
        "edges_created": 0, "edges_updated": 0, "edges_unchanged": 0,
        "edges_removed": 0, "edges_skipped": 0,
        "records_skipped": batch.skipped_records,
    }

    owned = owned_nodes_query(prefix)

    if not batch.nodes and not allow_empty:

        existing = len(list(session.scalars(owned.where(
            ContextNode.is_active.is_(True)
        ))))

        if existing:

            raise ConnectorDataError(
                f"Connector {name} returned no data but owns {existing} "
                "active node(s); refusing to deactivate them."
            )

    # ---- nodes -------------------------------------------------

    existing_nodes = load_nodes(session, [n.node_key for n in batch.nodes])

    for spec in batch.nodes:

        current = existing_nodes.get(spec.node_key)

        if current is not None and not is_owned(current, prefix):

            summary["nodes_left_unchanged"] += 1
            continue

        result = upsert_node(
            node_key=spec.node_key, node_type=spec.node_type,
            label=spec.label, source_type=SourceType.CONNECTOR,
            source_ref=spec.source_ref, properties=spec.properties,
            is_active=spec.is_active, session=session
        )

        summary[f"nodes_{result['change']}"] += 1

    # ---- edges -------------------------------------------------

    endpoints = {e.from_key for e in batch.edges}
    endpoints |= {e.to_key for e in batch.edges}

    known = load_nodes(session, endpoints)

    desired = set()

    for spec in batch.edges:

        if spec.from_key not in known or spec.to_key not in known:

            summary["edges_skipped"] += 1
            continue

        desired.add((spec.from_key, spec.edge_type, spec.to_key))

        result = upsert_edge(
            from_key=spec.from_key, edge_type=spec.edge_type,
            to_key=spec.to_key, source_type=SourceType.CONNECTOR,
            source_ref=spec.source_ref, confidence=spec.confidence,
            session=session
        )

        summary[f"edges_{result['change']}"] += 1

    # ---- no longer returned ------------------------------------

    summary["edges_removed"] = remove_stale_edges(
        session, prefix, desired
    )

    batch_keys = {n.node_key for n in batch.nodes}

    for node in list(session.scalars(owned.where(
        ContextNode.is_active.is_(True)
    ))):

        if node.node_key in batch_keys:
            continue

        upsert_node(
            node_key=node.node_key, node_type=node.node_type,
            label=node.label, source_type=node.source_type,
            source_ref=node.source_ref, properties=node.properties,
            is_active=False, session=session
        )

        summary["nodes_deactivated"] += 1

    return summary


def owned_nodes_query(prefix: str):

    return select(ContextNode).where(
        ContextNode.source_type == SourceType.CONNECTOR.value,
        ContextNode.source_ref.startswith(prefix, autoescape=True)
    )


def is_owned(node: ContextNode, prefix: str) -> bool:

    return (
        node.source_type == SourceType.CONNECTOR.value
        and (node.source_ref or "").startswith(prefix)
    )


def load_nodes(session, keys) -> dict[str, ContextNode]:

    keys = sorted(set(keys))
    found = {}

    for start in range(0, len(keys), LOOKUP_CHUNK):

        chunk = keys[start:start + LOOKUP_CHUNK]

        for node in session.scalars(
            select(ContextNode).where(ContextNode.node_key.in_(chunk))
        ):

            found[node.node_key] = node

    return found


def remove_stale_edges(session, prefix: str, desired: set) -> int:

    source = aliased(ContextNode)
    target = aliased(ContextNode)

    rows = session.execute(
        select(ContextEdge, source.node_key, target.node_key)
        .join(source, ContextEdge.from_node_id == source.id)
        .join(target, ContextEdge.to_node_id == target.id)
        .where(
            ContextEdge.source_type == SourceType.CONNECTOR.value,
            ContextEdge.source_ref.startswith(prefix, autoescape=True)
        )
    ).all()

    removed = 0

    for edge, from_key, to_key in rows:

        if (from_key, edge.edge_type, to_key) not in desired:

            session.delete(edge)
            removed += 1

    session.flush()

    return removed

"""
Enterprise connector abstraction.

A connector reads one external system and returns a ConnectorBatch:
the context graph nodes and edges that system contributes. It never
touches the database. connector_sync.sync_connector applies a batch
(one transaction, with audit events), so every connector gets the
same safety rules:

- A connector owns only what it created: nodes and edges whose
  source_ref starts with "<connector name>:" and whose source_type is
  "connector". Existing nodes owned by anything else are never
  overwritten, only linked to.
- Data the connector stops returning is deactivated (nodes) or
  removed (edges), but only after a complete, successful fetch.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ============================================================
# ERRORS (messages never contain credentials)
# ============================================================

class ConnectorError(Exception):
    """Base class for connector failures."""


class ConnectorConfigError(ConnectorError):
    """Missing or invalid configuration."""


class ConnectorAuthError(ConnectorError):
    """The external system rejected our credentials or permissions."""


class ConnectorRequestError(ConnectorError):
    """A request failed (network, timeout, HTTP error) after retries."""


class ConnectorDataError(ConnectorError):
    """The external system returned data that cannot be used safely."""


# ============================================================
# BATCH
# ============================================================

@dataclass(frozen=True)
class NodeSpec:

    node_key: str
    node_type: str
    label: str
    source_ref: str
    properties: dict | None = None
    is_active: bool = True


@dataclass(frozen=True)
class EdgeSpec:

    from_key: str
    edge_type: str
    to_key: str
    source_ref: str
    confidence: float | None = None


@dataclass
class ConnectorBatch:
    """
    Everything a connector contributes, as a complete snapshot of
    what the external system currently says.
    """

    nodes: list[NodeSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)

    # External records that could not be mapped (malformed, etc.).
    skipped_records: int = 0


# ============================================================
# CONNECTOR
# ============================================================

class Connector(ABC):

    # Short lowercase identifier, e.g. "entra". Also the prefix of
    # every source_ref this connector writes.
    name: str

    @abstractmethod
    def collect(self) -> ConnectorBatch:
        """
        Read the external system and return the complete batch.
        Raise a ConnectorError subclass on failure; a partial batch
        must never be returned.
        """

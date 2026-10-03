"""
Enterprise connectors: read an external system and map it into the
context graph.

    python -m app.services.connectors list
    python -m app.services.connectors sync entra
"""

from collections.abc import Mapping

from app.services.connectors.base import (
    Connector,
    ConnectorAuthError,
    ConnectorBatch,
    ConnectorConfigError,
    ConnectorDataError,
    ConnectorError,
    ConnectorRequestError,
    EdgeSpec,
    NodeSpec,
)
from app.services.connectors.entra import EntraConnector
from app.services.connectors.settings import EntraSettings, HttpSettings
from app.services.connectors.sync import sync_connector


# Connector name -> the environment variables that configure it.
CONNECTORS = {
    "entra": EntraSettings.REQUIRED,
}


def create_connector(name: str, env: Mapping | None = None) -> Connector:
    """Build a configured connector (reads os.environ by default)."""

    if name == "entra":

        return EntraConnector(
            EntraSettings.from_env(env),
            HttpSettings.from_env(env),
        )

    raise ConnectorConfigError(
        f"Unknown connector {name!r}. Available: {', '.join(CONNECTORS)}."
    )


__all__ = [
    "CONNECTORS",
    "Connector",
    "ConnectorAuthError",
    "ConnectorBatch",
    "ConnectorConfigError",
    "ConnectorDataError",
    "ConnectorError",
    "ConnectorRequestError",
    "EdgeSpec",
    "EntraConnector",
    "NodeSpec",
    "create_connector",
    "sync_connector",
]

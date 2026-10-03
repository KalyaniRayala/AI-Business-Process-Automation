import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Explicit constraint names keep Alembic migrations stable
# across SQLite and PostgreSQL.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# BIGINT primary keys only auto-increment as INTEGER on SQLite.
BigIntegerId = BigInteger().with_variant(Integer, "sqlite")

Money = Numeric(12, 2)

# JSONB on PostgreSQL (indexable, queryable), JSON elsewhere.
JsonDocument = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):

    metadata = MetaData(
        naming_convention=NAMING_CONVENTION
    )


# ============================================================
# EMPLOYEES
# ============================================================

class Employee(Base):

    __tablename__ = "employees"

    employee_id: Mapped[str] = mapped_column(
        String(20),
        primary_key=True
    )

    name: Mapped[str] = mapped_column(String(100))

    department: Mapped[str] = mapped_column(String(100))

    manager: Mapped[str] = mapped_column(String(100))

    annual_budget: Mapped[Decimal] = mapped_column(Money)


# ============================================================
# WORKFLOW RUNS
# ============================================================

class WorkflowRun(Base):

    __tablename__ = "workflow_runs"

    # LangGraph thread_id
    thread_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True
    )

    question: Mapped[str] = mapped_column(Text)

    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.employee_id")
    )

    expense_amount: Mapped[Decimal | None] = mapped_column(Money)

    # awaiting_approval | running | success | rejected | failed
    status: Mapped[str] = mapped_column(String(30))

    approval_status: Mapped[str | None] = mapped_column(String(30))

    failed_node: Mapped[str | None] = mapped_column(String(30))

    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


# ============================================================
# APPROVAL DECISIONS
# ============================================================

class ApprovalDecision(Base):

    __tablename__ = "approval_decisions"

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="decision_valid"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigIntegerId,
        primary_key=True,
        autoincrement=True
    )

    # Unique: a workflow can only be decided once.
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.thread_id"),
        unique=True
    )

    decision: Mapped[str] = mapped_column(String(10))

    approver: Mapped[str | None] = mapped_column(String(100))

    comment: Mapped[str | None] = mapped_column(Text)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )


# ============================================================
# REIMBURSEMENTS
# ============================================================

class Reimbursement(Base):

    __tablename__ = "reimbursements"

    __table_args__ = (
        CheckConstraint(
            "amount >= 0",
            name="amount_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigIntegerId,
        primary_key=True,
        autoincrement=True
    )

    transaction_id: Mapped[str] = mapped_column(
        String(40),
        unique=True
    )

    # Unique: one reimbursement per workflow (idempotent action).
    # Nullable so reimbursements can be recorded outside a workflow.
    thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.thread_id"),
        unique=True
    )

    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.employee_id")
    )

    amount: Mapped[Decimal] = mapped_column(Money)

    currency: Mapped[str] = mapped_column(
        String(3),
        server_default="USD"
    )

    department: Mapped[str] = mapped_column(String(100))

    manager: Mapped[str] = mapped_column(String(100))

    status: Mapped[str] = mapped_column(String(20))

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )


# ============================================================
# AUDIT EVENTS
# ============================================================

class AuditEvent(Base):
    """
    Append-only, chronological record of business-process events.

    Events reference business rows (thread_id, entity_type /
    entity_id) instead of copying them. thread_id has no foreign
    key on purpose: the audit history must survive archiving of
    business rows, and an audit write must never fail on it.
    """

    __tablename__ = "audit_events"

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('system', 'human', 'agent')",
            name="actor_type_valid"
        ),
        CheckConstraint(
            "status IN ('success', 'failure', 'pending', 'info')",
            name="status_valid"
        ),
        Index(
            "ix_audit_events_thread_id_occurred_at",
            "thread_id",
            "occurred_at"
        ),
        Index(
            "ix_audit_events_event_type_occurred_at",
            "event_type",
            "occurred_at"
        ),
        Index(
            "ix_audit_events_entity",
            "entity_type",
            "entity_id"
        ),
        Index(
            "ix_audit_events_occurred_at",
            "occurred_at"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigIntegerId,
        primary_key=True,
        autoincrement=True
    )

    # Stable external identifier
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        unique=True,
        default=uuid.uuid4
    )

    # Set by the application at the moment of the event, so
    # events inside one transaction keep distinct times.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now()
    )

    thread_id: Mapped[str | None] = mapped_column(String(64))

    event_type: Mapped[str] = mapped_column(String(50))

    # system | human | agent
    actor_type: Mapped[str] = mapped_column(String(20))

    actor_id: Mapped[str | None] = mapped_column(String(100))

    node: Mapped[str | None] = mapped_column(String(50))

    entity_type: Mapped[str | None] = mapped_column(String(50))

    entity_id: Mapped[str | None] = mapped_column(String(100))

    # success | failure | pending | info
    status: Mapped[str] = mapped_column(String(20))

    message: Mapped[str] = mapped_column(Text)

    details: Mapped[dict | None] = mapped_column(JsonDocument)

    error_type: Mapped[str | None] = mapped_column(String(100))

    error_message: Mapped[str | None] = mapped_column(Text)


# ============================================================
# CONTEXT GRAPH
# ============================================================

# Where a node or edge comes from. Checked by the database and by
# context_graph_service.
CONTEXT_SOURCE_TYPES_SQL = (
    "source_type IN ('system_of_record', 'document', 'curated', 'derived', "
    "'connector')"
)


def utc_now() -> datetime:

    return datetime.now(timezone.utc)


class ContextNode(Base):
    """
    A business-context entity (process, step, role, policy rule,
    employee, ...).

    Business tables stay the system of record: projected nodes
    point back to their source row through source_ref instead of
    copying its attributes. Node and edge types are controlled in
    context_graph_service. The graph is a rebuildable projection
    plus curated knowledge, so it is mutable (not append-only).
    """

    __tablename__ = "context_nodes"

    __table_args__ = (
        CheckConstraint(
            CONTEXT_SOURCE_TYPES_SQL,
            name="source_type_valid"
        ),
        Index("ix_context_nodes_node_type", "node_type"),
    )

    id: Mapped[int] = mapped_column(
        BigIntegerId,
        primary_key=True,
        autoincrement=True
    )

    # e.g. "employee:E102", "process_step:expense_reimbursement:07"
    node_key: Mapped[str] = mapped_column(String(200), unique=True)

    node_type: Mapped[str] = mapped_column(String(50))

    label: Mapped[str] = mapped_column(String(200))

    properties: Mapped[dict | None] = mapped_column(JsonDocument)

    # system_of_record | document | curated | derived
    source_type: Mapped[str] = mapped_column(String(30))

    # e.g. "employees:E102", "document:data/expense_process.txt#0"
    source_ref: Mapped[str | None] = mapped_column(String(300))

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now()
    )


class ContextEdge(Base):
    """
    A typed, directed relationship between two context nodes.
    One edge per (from, type, to); deleting a node deletes its edges.
    """

    __tablename__ = "context_edges"

    __table_args__ = (
        UniqueConstraint(
            "from_node_id",
            "edge_type",
            "to_node_id",
            name="uq_context_edges_from_type_to"
        ),
        CheckConstraint(
            "from_node_id <> to_node_id",
            name="no_self_loop"
        ),
        CheckConstraint(
            CONTEXT_SOURCE_TYPES_SQL,
            name="source_type_valid"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range"
        ),
        Index(
            "ix_context_edges_from_node_id_edge_type",
            "from_node_id",
            "edge_type"
        ),
        Index(
            "ix_context_edges_to_node_id_edge_type",
            "to_node_id",
            "edge_type"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigIntegerId,
        primary_key=True,
        autoincrement=True
    )

    from_node_id: Mapped[int] = mapped_column(
        BigIntegerId,
        ForeignKey("context_nodes.id", ondelete="CASCADE")
    )

    to_node_id: Mapped[int] = mapped_column(
        BigIntegerId,
        ForeignKey("context_nodes.id", ondelete="CASCADE")
    )

    edge_type: Mapped[str] = mapped_column(String(50))

    properties: Mapped[dict | None] = mapped_column(JsonDocument)

    source_type: Mapped[str] = mapped_column(String(30))

    source_ref: Mapped[str | None] = mapped_column(String(300))

    # For future extracted relationships; null for curated or
    # system-of-record edges.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now()
    )


# ============================================================
# ALEMBIC FILTER
# ============================================================

def include_managed_tables(name, type_, parent_names) -> bool:
    """
    Limit Alembic comparisons to tables defined here.

    Other tables in the same database, such as LangGraph's
    PostgresSaver checkpoint tables, manage their own schema
    and must never be altered or dropped by migrations.
    """

    if type_ == "table":
        return name in Base.metadata.tables

    return True

"""Initial schema: employees, workflow_runs, approval_decisions, reimbursements

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


BIG_INTEGER_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:

    # Offline SQL generation (--sql) has no database to inspect,
    # so it assumes an empty database.
    if context.is_offline_mode():
        existing_tables = []
    else:
        existing_tables = sa.inspect(op.get_bind()).get_table_names()

    if "employees" in existing_tables:

        # Adopt the employees table created by the earlier sqlite3
        # code (data/business.db): keep its rows, but rebuild it
        # with the same column types as a fresh install.
        with op.batch_alter_table("employees", recreate="always") as batch:

            batch.alter_column(
                "employee_id",
                existing_type=sa.Text(),
                type_=sa.String(20),
                nullable=False,
            )

            for column in ("name", "department", "manager"):

                batch.alter_column(
                    column,
                    existing_type=sa.Text(),
                    type_=sa.String(100),
                    existing_nullable=False,
                )

            batch.alter_column(
                "annual_budget",
                existing_type=sa.REAL(),
                type_=sa.Numeric(12, 2),
                existing_nullable=False,
            )

    else:

        op.create_table(
            "employees",
            sa.Column("employee_id", sa.String(20), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("department", sa.String(100), nullable=False),
            sa.Column("manager", sa.String(100), nullable=False),
            sa.Column("annual_budget", sa.Numeric(12, 2), nullable=False),
            sa.PrimaryKeyConstraint("employee_id", name="pk_employees"),
        )

    op.create_table(
        "workflow_runs",
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("employee_id", sa.String(20), nullable=True),
        sa.Column("expense_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("approval_status", sa.String(30), nullable=True),
        sa.Column("failed_node", sa.String(30), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.employee_id"],
            name="fk_workflow_runs_employee_id_employees",
        ),
        sa.PrimaryKeyConstraint("thread_id", name="pk_workflow_runs"),
    )

    op.create_table(
        "approval_decisions",
        sa.Column("id", BIG_INTEGER_ID, autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("approver", sa.String(100), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')",
            # Expanded by the naming convention to
            # ck_approval_decisions_decision_valid
            name="decision_valid",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["workflow_runs.thread_id"],
            name="fk_approval_decisions_thread_id_workflow_runs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decisions"),
        sa.UniqueConstraint(
            "thread_id",
            name="uq_approval_decisions_thread_id",
        ),
    )

    op.create_table(
        "reimbursements",
        sa.Column("id", BIG_INTEGER_ID, autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.String(40), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("employee_id", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            server_default="USD",
            nullable=False,
        ),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("manager", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount >= 0",
            # Expanded by the naming convention to
            # ck_reimbursements_amount_non_negative
            name="amount_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.employee_id"],
            name="fk_reimbursements_employee_id_employees",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["workflow_runs.thread_id"],
            name="fk_reimbursements_thread_id_workflow_runs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reimbursements"),
        sa.UniqueConstraint(
            "transaction_id",
            name="uq_reimbursements_transaction_id",
        ),
        sa.UniqueConstraint(
            "thread_id",
            name="uq_reimbursements_thread_id",
        ),
    )


def downgrade() -> None:

    op.drop_table("reimbursements")
    op.drop_table("approval_decisions")
    op.drop_table("workflow_runs")
    op.drop_table("employees")

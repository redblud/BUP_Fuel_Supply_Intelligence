"""Allocation lifecycle: one row per observed (run, allocation, status).

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allocation_transitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.String(200), nullable=False),
        sa.Column("allocation_id", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("tick", sa.Integer, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.UniqueConstraint("run_id", "allocation_id", "status", name="uq_transition_run_alloc_status"),
    )
    op.create_index("ix_transition_run_key", "allocation_transitions", ["run_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_table("allocation_transitions")

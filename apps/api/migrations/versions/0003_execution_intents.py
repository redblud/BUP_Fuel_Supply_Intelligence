"""Durable execution intents and automation settings.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_intents",
        sa.Column("recommendation_id", sa.String(200), primary_key=True),
        sa.Column("run_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_tick", sa.Integer, nullable=False),
        sa.Column("allocation_id", sa.Integer, nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("request_body", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "automation_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("automation_settings")
    op.drop_table("execution_intents")

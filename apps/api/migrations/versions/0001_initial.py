"""Initial schema: system events, simulation runs, demand and station observations, last trusted snapshot.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Databases created by the foundation create_all already have system_events.
    if "system_events" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "system_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("kind", sa.String(64), nullable=False),
            sa.Column("detail", sa.String(500), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    op.create_table(
        "simulation_runs",
        sa.Column("run_id", sa.String(200), primary_key=True),
        sa.Column("scenario_id", sa.String(100), nullable=False),
        sa.Column("seed", sa.Integer, nullable=False),
        sa.Column("first_tick", sa.Integer, nullable=False),
        sa.Column("last_tick", sa.Integer, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "demand_observations",
        sa.Column("run_id", sa.String(200), primary_key=True),
        sa.Column("station_id", sa.String(100), primary_key=True),
        sa.Column("fuel_type", sa.String(16), primary_key=True),
        sa.Column("tick", sa.Integer, primary_key=True),
        sa.Column("demand_liters", sa.Float, nullable=False),
        sa.Column("served_liters", sa.Float, nullable=False),
        sa.Column("unmet_liters", sa.Float, nullable=False),
    )
    op.create_index("ix_demand_run_tick", "demand_observations", ["run_id", "tick"])
    op.create_table(
        "station_state_observations",
        sa.Column("run_id", sa.String(200), primary_key=True),
        sa.Column("tick", sa.Integer, primary_key=True),
        sa.Column("station_id", sa.String(100), primary_key=True),
        sa.Column("fuel_type", sa.String(16), primary_key=True),
        sa.Column("inventory", sa.Float, nullable=False),
        sa.Column("station_status", sa.String(16), nullable=False),
    )
    op.create_table(
        "snapshots",
        sa.Column("run_id", sa.String(200), primary_key=True),
        sa.Column("tick", sa.Integer, nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freshness", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
    )


def downgrade() -> None:
    for table in ("snapshots", "station_state_observations", "demand_observations", "simulation_runs", "system_events"):
        op.drop_table(table)

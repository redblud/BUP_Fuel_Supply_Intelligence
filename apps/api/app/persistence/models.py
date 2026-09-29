"""SQLAlchemy tables. Schema changes go through Alembic migrations (alembic/versions), never create_all."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(100))
    seed: Mapped[int] = mapped_column(Integer)
    first_tick: Mapped[int] = mapped_column(Integer)
    last_tick: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DemandObservationRow(Base):
    __tablename__ = "demand_observations"
    __table_args__ = (Index("ix_demand_run_tick", "run_id", "tick"),)

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    station_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    fuel_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    demand_liters: Mapped[float] = mapped_column(Float)
    served_liters: Mapped[float] = mapped_column(Float)
    unmet_liters: Mapped[float] = mapped_column(Float)


class StationStateObservation(Base):
    __tablename__ = "station_state_observations"

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    fuel_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    inventory: Mapped[float] = mapped_column(Float)
    station_status: Mapped[str] = mapped_column(String(16))


class AllocationTransitionRow(Base):
    """One observed status of a simulator allocation; `payload` is the allocation as observed (JSON)."""

    __tablename__ = "allocation_transitions"
    __table_args__ = (
        UniqueConstraint("run_id", "allocation_id", "status", name="uq_transition_run_alloc_status"),
        Index("ix_transition_run_key", "run_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(200))
    allocation_id: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))
    tick: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[str] = mapped_column(Text)


class SnapshotRow(Base):
    """The last trusted NetworkState per run (one row, replaced on every persisted tick)."""

    __tablename__ = "snapshots"

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    freshness: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
